from django.contrib import admin
from .models import Product, ProductImage, ProductSize, Order, OrderItem


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3


class ProductSizeInline(admin.TabularInline):
    model = ProductSize
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'sold_out', 'created_at')
    list_filter = ('sold_out', 'created_at')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, ProductSizeInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'size', 'quantity', 'price')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('display_order_number', 'full_name', 'email', 'total_price', 'is_paid', 'created_at')
    search_fields = ('full_name', 'email', 'stripe_session_id')
    list_filter = ('is_paid', 'created_at')
    readonly_fields = ('display_order_number', 'created_at', 'stripe_session_id')
    inlines = [OrderItemInline]

    def display_order_number(self, obj):
        return obj.order_number
    display_order_number.short_description = 'Order Number'