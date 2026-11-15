"""Bounded online lookup for an unknown UPC/EAN/GTIN barcode.

Sources are tried in order (see EXTERNAL_LOOKUP_PROVIDERS): Open Food Facts, Open Products Facts, UPCitemdb,
and Barcode Lookup. The first matching product supplies its name; later providers may fill missing image or
bottle-capacity data. Your own catalogue is always checked before anything here is called."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, UnidentifiedImageError
from django.core.files.base import ContentFile
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

from . import codes

LOOKUP_TIMEOUT_SECONDS = 3
MAX_RESPONSE_BYTES = 256 * 1024
NOT_FOUND_CACHE_SECONDS = 6 * 60 * 60
DETAILS_CACHE_SECONDS = 6 * 60 * 60
IMAGE_TIMEOUT_SECONDS = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_HOSTS = {
    "images.barcodelookup.com",
    "images.openfoodfacts.org",
    "static.openfoodfacts.org",
    "images.openproductsfacts.org",
    "static.openproductsfacts.org",
    "images.upcitemdb.com",
    "www.upcitemdb.com",
}
IMAGE_FORMAT_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
_VOLUME_RE = re.compile(
    r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*"
    r"(ml|millilit(?:er|re)s?|cl|centilit(?:er|re)s?|l|lit(?:er|re)s?)\b",
    re.IGNORECASE,
)


class ExternalLookupError(Exception):
    """The external product source could not be queried safely."""


class ExternalUnreachable(ExternalLookupError):
    """The shop server cannot reach the internet, so asking the other sources would only add waiting."""


class ExternalLookupDisabled(ExternalLookupError):
    """External product lookup has not been enabled in the shop environment."""


class ExternalLookupConfigurationError(ExternalLookupError):
    """A selected provider is missing credentials or has invalid configuration."""


class ExternalImageError(ExternalLookupError):
    """An online product image could not be safely downloaded or stored."""


def is_supported_gtin(raw_code):
    code = codes.from_scanner(raw_code)
    if len(code) not in (8, 12, 13, 14) or not code.isdigit() or codes.is_internal(code):
        return False
    body, check_digit = code[:-1], int(code[-1])
    total = sum(int(digit) * (3 if offset % 2 == 0 else 1)
                for offset, digit in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check_digit


def _clean_text(value, limit):
    spaced = " ".join(str(value or "").split())              # newlines/tabs between words become single spaces
    return "".join(char for char in spaced if char.isprintable())[:limit].strip()


def _image_urls(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(url for item in value if (url := _clean_text(item, 1000))))[:5]


def _bottle_capacity_ml(*values):
    for value in values:
        for amount_text, unit in _VOLUME_RE.findall(str(value or "")):
            try:
                amount = Decimal(amount_text.replace(",", "."))
            except InvalidOperation:
                continue
            unit = unit.lower()
            multiplier = 10 if unit.startswith("cl") or unit.startswith("centil") else (
                1000 if unit == "l" or unit.startswith("lit") else 1
            )
            capacity = amount * multiplier
            if Decimal("1") <= capacity <= Decimal("20000"):
                return capacity.quantize(Decimal("0.01"))
    return None


def _product_details(payload, requested_code, label="Open Food Facts"):
    """Parse the Open Food Facts / Open Products Facts v3 answer (they share one format)."""
    if not isinstance(payload, dict):
        raise ExternalLookupError(f"{label} returned an invalid response.")
    if payload.get("status") != "success":
        result = payload.get("result")
        if isinstance(result, dict) and result.get("id") == "product_not_found":
            return None
        raise ExternalLookupError(f"{label} returned an invalid response.")
    product = payload.get("product")
    if not isinstance(product, dict):
        return None

    returned_raw = product.get("code") or payload.get("code") or requested_code
    returned_code = codes.from_scanner(str(returned_raw))
    if returned_code not in codes.lookup_candidates(requested_code):
        raise ExternalLookupError(f"{label} returned a different barcode.")

    name = (_clean_text(product.get("product_name"), 160)
            or _clean_text(product.get("product_name_en"), 160))
    if not name:
        return None
    brand = _clean_text(product.get("brands"), 80).split(",")[0].strip()
    details = {"name": name, "brand": brand}
    images = _image_urls([product.get("image_front_url"), product.get("image_url")])
    capacity = _bottle_capacity_ml(name, product.get("quantity"), product.get("product_quantity"))
    if images:
        details["image_urls"] = images
    if capacity is not None:
        details["bottle_capacity_ml"] = capacity
    return details


def _upcitemdb_details(payload, requested_code, label="UPCitemdb"):
    """Parse UPCitemdb's trial lookup: {"code": "OK", "total": 1, "items": [{"title", "brand", "ean", "upc"}]}."""
    if not isinstance(payload, dict):
        raise ExternalLookupError(f"{label} returned an invalid response.")
    status = str(payload.get("code") or "").upper()
    if status in ("EXCEED_LIMIT", "TOO_FAST"):
        raise ExternalLookupError(f"{label} daily or per-minute limit reached; it will work again later.")
    if status == "INVALID_UPC":
        return None
    if status != "OK":
        raise ExternalLookupError(f"{label} returned an invalid response.")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    item = items[0]
    if not isinstance(item, dict):
        raise ExternalLookupError(f"{label} returned an invalid response.")
    returned = {codes.from_scanner(str(item.get(key) or "")) for key in ("ean", "upc")} - {""}
    if returned and not returned & set(codes.lookup_candidates(requested_code)):
        raise ExternalLookupError(f"{label} returned a different barcode.")
    name = _clean_text(item.get("title"), 160)
    if not name:
        return None
    details = {"name": name, "brand": _clean_text(item.get("brand"), 80)}
    images = _image_urls(item.get("images"))
    capacity = _bottle_capacity_ml(name, item.get("size"))
    if images:
        details["image_urls"] = images
    if capacity is not None:
        details["bottle_capacity_ml"] = capacity
    return details


def _barcodelookup_details(payload, requested_code, label="Barcode Lookup"):
    """Parse Barcode Lookup's v3 products response for the requested barcode."""
    if not isinstance(payload, dict):
        raise ExternalLookupError(f"{label} returned an invalid response.")
    products = payload.get("products")
    if not isinstance(products, list):
        raise ExternalLookupError(f"{label} returned an invalid response.")
    if not products:
        return None

    candidates = set(codes.lookup_candidates(requested_code))
    for product in products:
        if not isinstance(product, dict):
            raise ExternalLookupError(f"{label} returned an invalid response.")
        returned_values = [str(product.get("barcode_number") or "")]
        returned_values.extend(re.findall(r"\d{7,14}", str(product.get("barcode_formats") or "")))
        returned_codes = set()
        for value in returned_values:
            returned_code = codes.from_scanner(value)
            if returned_code:
                returned_codes.add(returned_code)
                if len(returned_code) == 11 and returned_code.isdigit():
                    returned_codes.add("0" + returned_code)
        if not returned_codes & candidates:
            continue
        name = _clean_text(product.get("title"), 160)
        if not name:
            continue
        details = {"name": name, "brand": _clean_text(product.get("brand"), 80)}
        images = _image_urls(product.get("images"))
        capacity = _bottle_capacity_ml(name, product.get("size"))
        if images:
            details["image_urls"] = images
        if capacity is not None:
            details["bottle_capacity_ml"] = capacity
        return details
    raise ExternalLookupError(f"{label} returned a different barcode.")


def _barcodelookup_url(code):
    from core import integration_settings

    try:
        api_key = integration_settings.barcode_lookup()["API_KEY"].strip()
    except ImproperlyConfigured as exc:
        raise ExternalLookupConfigurationError(f"Barcode Lookup configuration is invalid: {exc}") from exc
    if not api_key:
        raise ExternalLookupConfigurationError("Barcode Lookup API key is not configured.")
    query = urllib.parse.urlencode({"barcode": code, "key": api_key})
    return f"https://api.barcodelookup.com/v3/products?{query}"


def _approved_image_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in IMAGE_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


class _ImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _approved_image_url(newurl):
            raise ExternalImageError("The product image redirected to an unapproved host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _image_content(body, code):
    try:
        with Image.open(BytesIO(body)) as image:
            image_format = image.format
            width, height = image.size
            if image_format not in IMAGE_FORMAT_EXTENSIONS:
                raise ExternalImageError("The product image format is not supported.")
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ExternalImageError("The product image dimensions are too large.")
            image.verify()
    except ExternalImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ExternalImageError("The product image is invalid or unreadable.") from exc
    extension = IMAGE_FORMAT_EXTENSIONS[image_format]
    return ContentFile(body, name=f"external-{code}.{extension}")


def download_product_image(image_urls, code):
    """Fetch one verified image from an approved HTTPS image host; never trust redirects or unbounded files."""
    urls = _image_urls(image_urls)
    if not urls:
        return None
    approved_urls = [url for url in urls if _approved_image_url(url)]
    if not approved_urls:
        raise ExternalImageError("The product image is not hosted on an approved image server.")

    last_error = None
    for url in approved_urls:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "image/jpeg,image/png,image/webp",
                "User-Agent": "WinesPOS/1.0 (https://github.com/nabiswa-cmd/POS)",
            },
        )
        opener = urllib.request.build_opener(_ImageRedirectHandler())
        try:
            with opener.open(request, timeout=IMAGE_TIMEOUT_SECONDS) as response:
                if not _approved_image_url(response.geturl()):
                    raise ExternalImageError("The product image redirected to an unapproved host.")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > MAX_IMAGE_BYTES:
                            raise ExternalImageError("The product image is larger than the allowed limit.")
                    except ValueError as exc:
                        raise ExternalImageError("The product image response has an invalid size.") from exc
                body = response.read(MAX_IMAGE_BYTES + 1)
            if len(body) > MAX_IMAGE_BYTES:
                raise ExternalImageError("The product image is larger than the allowed limit.")
            return _image_content(body, code)
        except ExternalImageError as exc:
            last_error = exc
        except urllib.error.HTTPError as exc:
            last_error = ExternalImageError(f"The product image server returned HTTP {exc.code}.")
        except (urllib.error.URLError, TimeoutError, OSError):
            last_error = ExternalImageError("Could not reach the product image server.")

    raise last_error


_OPEN_FACTS_QUERY = urllib.parse.urlencode({
    "fields": "code,product_name,product_name_en,brands,image_front_url,image_url,quantity,product_quantity",
})

# Order here is only the default; the shop environment decides what runs and in what order.
PROVIDERS = {
    "openfoodfacts": {
        "label": "Open Food Facts",
        "url": "https://world.openfoodfacts.org/api/v3/product/{code}?" + _OPEN_FACTS_QUERY,
        "parse": _product_details,
        "not_found_http": (404,),
    },
    "openproductsfacts": {
        "label": "Open Products Facts",
        "url": "https://world.openproductsfacts.org/api/v3/product/{code}?" + _OPEN_FACTS_QUERY,
        "parse": _product_details,
        "not_found_http": (404,),
    },
    "upcitemdb": {
        "label": "UPCitemdb",
        "url": "https://api.upcitemdb.com/prod/trial/lookup?upc={code}",
        "parse": _upcitemdb_details,
        "not_found_http": (404, 400),
    },
    "barcodelookup": {
        "label": "Barcode Lookup",
        "url_builder": _barcodelookup_url,
        "parse": _barcodelookup_details,
        "not_found_http": (404,),
        "api_key_required": True,
    },
}
DEFAULT_PROVIDERS = ("openfoodfacts", "openproductsfacts", "upcitemdb", "barcodelookup")


def enabled_providers():
    """Provider keys to try, in order, from EXTERNAL_LOOKUP_PROVIDERS. Unknown names are ignored."""
    wanted = getattr(settings, "EXTERNAL_LOOKUP_PROVIDERS", None) or DEFAULT_PROVIDERS
    seen = []
    for name in wanted:
        name = str(name).strip().lower()
        if name in PROVIDERS and name not in seen:
            seen.append(name)
    return seen


def _lookup_one(name, code):
    """Ask one source. Returns details, None for a miss, or raises ExternalLookupError."""
    provider = PROVIDERS[name]
    label = provider["label"]
    cache_key = f"extlookup:{name}:{code}"
    cached = cache.get(cache_key)
    if cached is True:
        return None                                 # this source said "not found" recently
    if isinstance(cached, dict):
        return dict(cached)

    url_builder = provider.get("url_builder")
    url = url_builder(code) if url_builder else provider["url"].format(code=code)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "WinesPOS/1.0 (https://github.com/nabiswa-cmd/POS)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=LOOKUP_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in provider["not_found_http"]:
            cache.set(cache_key, True, timeout=NOT_FOUND_CACHE_SECONDS)
            return None
        if exc.code in (401, 403) and provider.get("api_key_required"):
            raise ExternalLookupConfigurationError(
                f"{label} rejected the API key; check the integration settings.") from None
        if exc.code == 429:
            message = f"{label} is limiting requests right now; try again later."
            if provider.get("api_key_required"):
                raise ExternalLookupError(message) from None
            raise ExternalLookupError(message) from exc
        if provider.get("api_key_required"):
            raise ExternalLookupError(f"{label} returned HTTP {exc.code}; retry the barcode lookup.") from None
        raise ExternalLookupError(f"{label} returned HTTP {exc.code}; retry the barcode lookup.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if provider.get("api_key_required"):
            raise ExternalUnreachable(f"Cannot reach {label}. The shop server may be offline.") from None
        raise ExternalUnreachable(f"Cannot reach {label}. The shop server may be offline.") from exc

    if len(body) > MAX_RESPONSE_BYTES:
        raise ExternalLookupError(f"{label} returned an unexpectedly large response.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalLookupError(f"{label} returned unreadable product data.") from exc

    details = provider["parse"](payload, code, label)
    if details is None:
        cache.set(cache_key, True, timeout=NOT_FOUND_CACHE_SECONDS)
        return None
    details["source"] = label
    cache.set(cache_key, details, timeout=DETAILS_CACHE_SECONDS)
    return details


def _merge_missing_metadata(primary, additional):
    if not primary.get("image_urls") and additional.get("image_urls"):
        primary["image_urls"] = additional["image_urls"]
    if primary.get("bottle_capacity_ml") is None and additional.get("bottle_capacity_ml") is not None:
        primary["bottle_capacity_ml"] = additional["bottle_capacity_ml"]


def lookup_barcode(raw_code):
    """Return sanitized details from the first product match, enriching missing image/capacity from later sources.

    Sources run in order. A source that fails (limit reached, bad answer) is skipped. If the server is offline,
    enrichment stops at once; a match already found is still returned. An error is raised only when no source
    could provide a product."""
    if not settings.OPEN_FOOD_FACTS_ENABLED:
        raise ExternalLookupDisabled("External barcode lookup is disabled; ask the administrator to enable it.")

    code = codes.from_scanner(raw_code)
    if not is_supported_gtin(code):
        return None

    failures, configuration_failures, answered = [], [], False
    first_match = None
    for name in enabled_providers():
        try:
            details = _lookup_one(name, code)
        except ExternalUnreachable as exc:
            if first_match is None:
                raise
            first_match["metadata_warning"] = str(exc)
            break
        except ExternalLookupConfigurationError as exc:
            configuration_failures.append(exc)
            continue
        except ExternalLookupError as exc:
            failures.append(exc)
            continue
        answered = True
        if details is not None:
            if first_match is None:
                first_match = details
            else:
                _merge_missing_metadata(first_match, details)
            if first_match.get("image_urls") and first_match.get("bottle_capacity_ml") is not None:
                return first_match
    if first_match is not None:
        if configuration_failures:
            first_match["metadata_warning"] = str(configuration_failures[0])
        elif failures and (not first_match.get("image_urls") or first_match.get("bottle_capacity_ml") is None):
            first_match["metadata_warning"] = str(failures[0])
        elif not first_match.get("image_urls") or first_match.get("bottle_capacity_ml") is None:
            missing = []
            if not first_match.get("image_urls"):
                missing.append("product image")
            if first_match.get("bottle_capacity_ml") is None:
                missing.append("bottle capacity")
            first_match["metadata_warning"] = f"Online providers did not supply {' or '.join(missing)}."
        return first_match
    if failures and not answered:
        raise failures[0]
    if configuration_failures and not answered:
        raise configuration_failures[0]
    return None
