import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import ReceiptPrintJob


def claim_receipt_print_job():
    with transaction.atomic():
        ReceiptPrintJob.objects.filter(
            status=ReceiptPrintJob.PRINTING, updated_at__lt=timezone.now() - timedelta(minutes=5)
        ).update(status=ReceiptPrintJob.QUEUED, error="Recovered after the printer worker stopped.")
        job = (ReceiptPrintJob.objects.select_for_update()
               .filter(status=ReceiptPrintJob.QUEUED).order_by("created_at", "id").first())
        if job is None:
            return None
        job.status = ReceiptPrintJob.PRINTING
        job.attempts += 1
        job.error = ""
        job.save(update_fields=["status", "attempts", "error", "updated_at"])
        return job.pk


def print_receipt_payload(payload, printer_name):
    from PySide6.QtCore import QByteArray, QMarginsF, QPointF, QRectF, QSizeF
    from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    from PySide6.QtSvg import QSvgRenderer
    from django.contrib.staticfiles import finders

    if printer_name not in QPrinterInfo.availablePrinterNames():
        raise ValueError(f"Receipt printer '{printer_name}' is not installed on the server computer.")

    paper_mm = 58 if payload.get("paper_mm") == 58 else 80
    lines = payload.get("lines")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("The receipt print job has invalid text.")

    logo_path = finders.find("core/receipt-logo.png")
    logo = QImage(str(logo_path)) if logo_path else QImage()
    qr_svg = payload.get("qr_svg") or ""
    has_qr = isinstance(qr_svg, str) and bool(qr_svg)
    line_height_mm = 3.6
    logo_height_mm = 18 if not logo.isNull() else 0
    qr_height_mm = 34 if has_qr else 0
    page_height_mm = 8 + logo_height_mm + len(lines) * line_height_mm + qr_height_mm

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(printer_name)
    printer.setResolution(203)
    printer.setPageSize(QPageSize(QSizeF(paper_mm, page_height_mm), QPageSize.Unit.Millimeter))
    printer.setPageMargins(QMarginsF(2, 2, 2, 2), QPageLayout.Unit.Millimeter)

    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError(f"Could not start receipt printer '{printer_name}'. Check that it is ready.")
    try:
        page = printer.pageRect(QPrinter.Unit.DevicePixel)
        y = page.top()
        if not logo.isNull():
            height = printer.resolution() * logo_height_mm / 25.4
            width = height * logo.width() / logo.height()
            painter.drawImage(QRectF(page.left() + (page.width() - width) / 2, y, width, height), logo)
            y += height + printer.resolution() / 25.4

        font = QFont("Courier New")
        char_count = max(1, max((len(line) for line in lines), default=32))
        font_points = min(8, (paper_mm - 4) * 72 / (char_count * 0.62 * 25.4))
        font.setPointSizeF(max(6, font_points))
        painter.setFont(font)
        metrics = QFontMetricsF(font, painter.device())
        line_height = max(metrics.height(), printer.resolution() * line_height_mm / 25.4)
        for line in lines:
            painter.drawText(QPointF(page.left(), y + metrics.ascent()), line)
            y += line_height

        if has_qr:
            renderer = QSvgRenderer(QByteArray(qr_svg.encode("utf-8")))
            if not renderer.isValid():
                raise ValueError("The receipt QR code is invalid.")
            size = printer.resolution() * 30 / 25.4
            target = QRectF(page.left() + (page.width() - size) / 2, y, size, size)
            renderer.render(painter, target)
    finally:
        painter.end()


def finish_receipt_print_job(job_id, *, error=""):
    from django.utils import timezone

    status = ReceiptPrintJob.FAILED if error else ReceiptPrintJob.PRINTED
    ReceiptPrintJob.objects.filter(pk=job_id, status=ReceiptPrintJob.PRINTING).update(
        status=status, error=error[:1000], printed_at=None if error else timezone.now())


def configured_printer():
    from core.models import ReceiptSettings

    configured = (ReceiptSettings.objects.filter(pk=1).values_list("printer_name", flat=True).first() or "").strip()
    return configured or os.environ.get("WINESPOS_RECEIPT_PRINTER", "").strip()
