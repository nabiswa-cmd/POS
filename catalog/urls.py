from django.urls import path

from . import views

urlpatterns = [
    path("manage/", views.manage_home, name="manage-home"),
    path("manage/products/", views.product_list, name="product-list"),
    path("manage/products/new/", views.product_new, name="product-new"),
    path("manage/products/<int:pk>/", views.product_detail, name="product-detail"),
    path("manage/products/<int:pk>/codes/", views.product_add_code, name="product-add-code"),
    path("manage/products/<int:pk>/codes/generate/", views.product_generate_code, name="product-generate-code"),
    path("manage/products/<int:pk>/packaging/", views.product_add_packaging, name="product-add-packaging"),
    path("manage/products/<int:pk>/packaging/<int:pack_id>/toggle/", views.product_toggle_packaging,
         name="product-toggle-packaging"),
    path("manage/lists/", views.lists, name="lists"),
    path("manage/suppliers/", views.supplier_list, name="supplier-list"),
    path("manage/suppliers/new/", views.supplier_edit, name="supplier-new"),
    path("manage/suppliers/<int:pk>/", views.supplier_edit, name="supplier-edit"),
    path("manage/shelves/", views.shelf_list, name="shelf-list"),
    path("manage/shelves/new/", views.shelf_edit, name="shelf-new"),
    path("manage/shelves/<int:pk>/", views.shelf_detail, name="shelf-detail"),
    path("manage/shelves/<int:pk>/edit/", views.shelf_edit, name="shelf-edit"),
    path("manage/labels/", views.label_select, name="label-select"),
    path("manage/labels/print/", views.label_print, name="label-print"),
    path("api/catalog/lookup/", views.api_lookup, name="api-lookup"),
    path("api/catalog/search/", views.api_search, name="api-search"),
]
