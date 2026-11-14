Change till + bottle-cap loader. Copy each file into the same path in your project.

  tills/views.py                       new change_till endpoint (78 lines added, nothing else touched)
  tills/urls.py                        1 line: api/pos/till/change/
  tills/test_change_till.py            NEW: 8 tests for the endpoint
  core/templates/core/app.html         till buttons on steps 2 and 3, "Use a different till" on the lock screen, loader
  sales/templates/sales/pos.html       3 lines in <head>: loads the loader
  core/static/core/posloader.css       NEW
  core/templates/core/base.html        loader on all manager / receiving / catalog / inventory pages
  templates/admin/base_site.html       loader on the Django admin screens
  tills/templates/tills/cashup_pos.html  loader on the close-shift page
  sales/templates/sales/sell.html      loader on the older sell page
  core/static/core/posloader.js        NEW

After copying, run:   python manage.py collectstatic --noinput
(your project serves from staticfiles/, so the two new posloader files must be collected)

Optional setting (config/settings.py):
  POS_TILL_CHANGE_REAUTH = True   # supervisor must authorize again when the till is changed (default False)

Tune the loader in JS:  PosLoader.config.minOverlay (default 1800 ms), PosLoader.config.minButton (700 ms)

The loader now starts by itself on every page that loads posloader.js: link clicks and form posts show the big cap
while the next page loads (after 200 ms, so instant pages never flash it), and every clicked button shows the little cap.
Opt out with data-no-loader on a button, link or form.  PosLoader.config.navDelay / navWatchdog tune it.
