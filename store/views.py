from django.shortcuts import render, get_object_or_404, redirect
from django.conf import settings
from decimal import Decimal
import stripe

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import Product, ProductSize, Order, OrderItem
from .forms import CheckoutForm

stripe.api_key = settings.STRIPE_SECRET_KEY


def get_payment_method_label(session):
    try:
        payment_intent_id = session.get('payment_intent')
        if not payment_intent_id:
            return "CARD"

        payment_intent = stripe.PaymentIntent.retrieve(
            payment_intent_id,
            expand=['latest_charge']
        )

        latest_charge = payment_intent.get('latest_charge')
        if not latest_charge:
            return "CARD"

        payment_method_details = latest_charge.get('payment_method_details', {})
        pm_type = payment_method_details.get('type')

        if pm_type == 'card':
            card = payment_method_details.get('card', {})
            wallet = card.get('wallet')

            if wallet:
                wallet_type = wallet.get('type')
                if wallet_type == 'apple_pay':
                    return "APPLE PAY"
                if wallet_type == 'google_pay':
                    return "GOOGLE PAY"
                if wallet_type == 'samsung_pay':
                    return "SAMSUNG PAY"

            brand = card.get('brand')
            if brand:
                return brand.replace('_', ' ').upper()

            return "CARD"

        if pm_type:
            return pm_type.replace('_', ' ').upper()

        return "CARD"

    except Exception as e:
        print("Could not determine payment method:", str(e))
        return "CARD"


def send_order_confirmation_email(order, session):
    import resend

    resend.api_key = settings.RESEND_API_KEY

    order_items = order.items.all()
    payment_method_label = get_payment_method_label(session)

    subject = f"CROWNVII Order Confirmation #{order.order_number}"

    context = {
        'order': order,
        'order_items': order_items,
        'tracking_url': 'https://crownvii.com/contact/',
        'payment_method': payment_method_label,
        'subtotal': order.total_price,
        'delivery_cost': 0,
        'delivery_discount': 0,
        'discount': 0,
        'total': order.total_price,
    }

    text_content = render_to_string('store/emails/order_confirmation.txt', context)
    html_content = render_to_string('store/emails/order_confirmation.html', context)

    resend.Emails.send({
        "from": settings.DEFAULT_FROM_EMAIL,
        "to": [order.email],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    })


def home(request):
    sort = request.GET.get('sort', 'new')

    products = Product.objects.all()

    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'name':
        products = products.order_by('name')
    else:
        products = products.order_by('-created_at')

    favourites = request.session.get('favourites', [])

    return render(request, 'store/index.html', {
        'products': products,
        'current_sort': sort,
        'favourites': favourites,
    })

def collection(request):
    products = Product.objects.all().order_by('-created_at')

    return render(
        request,
        'store/collection.html',
        {
            'products': products
        }
    )


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug)
    return render(request, 'store/product_detail.html', {'product': product})


def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if request.method != 'POST':
        return redirect('product_detail', slug=product.slug)

    selected_size = request.POST.get('size', '').strip()

    if product.sizes.exists():
        if not selected_size:
            return redirect('product_detail', slug=product.slug)

        size_obj = get_object_or_404(ProductSize, product=product, size=selected_size)

        if size_obj.stock < 1:
            return redirect('product_detail', slug=product.slug)
    else:
        selected_size = None

    cart = request.session.get('cart', {})
    cart_key = f"{product_id}_{selected_size}" if selected_size else str(product_id)

    if cart_key in cart:
        cart[cart_key]['quantity'] += 1
    else:
        cart[cart_key] = {
            'product_id': product_id,
            'size': selected_size,
            'quantity': 1,
        }

    request.session['cart'] = cart
    request.session.modified = True
    return redirect('cart')


def cart_view(request):
    cart = request.session.get('cart', {})
    cart_items = []
    total = Decimal('0.00')

    for cart_key, item_data in cart.items():
        product = get_object_or_404(Product, id=item_data['product_id'])
        quantity = int(item_data['quantity'])
        size = item_data.get('size')

        item_total = product.price * quantity
        total += item_total

        cart_items.append({
            'cart_key': cart_key,
            'product': product,
            'size': size,
            'quantity': quantity,
            'item_total': item_total,
        })

    return render(request, 'store/cart.html', {
        'cart_items': cart_items,
        'total': total,
    })


def remove_from_cart(request, cart_key):
    cart = request.session.get('cart', {})

    if cart_key in cart:
        del cart[cart_key]

    request.session['cart'] = cart
    request.session.modified = True
    return redirect('cart')


def update_cart_quantity(request, cart_key, action):
    cart = request.session.get('cart', {})

    if cart_key in cart:
        if action == 'increase':
            cart[cart_key]['quantity'] += 1

        elif action == 'decrease':
            cart[cart_key]['quantity'] -= 1

            if cart[cart_key]['quantity'] <= 0:
                del cart[cart_key]

    request.session['cart'] = cart
    request.session.modified = True
    return redirect('cart')


def terms(request):
    return render(request, 'store/terms.html')


def refund(request):
    return render(request, 'store/refund.html')


def contact(request):
    return render(request, 'store/contact.html')


def privacy(request):
    return render(request, 'store/privacy.html')


def checkout_view(request):
    cart = request.session.get('cart', {})
    cart_items = []
    total = Decimal('0.00')

    if not cart:
        return redirect('cart')

    for cart_key, item_data in cart.items():
        product = get_object_or_404(Product, id=item_data['product_id'])
        quantity = int(item_data['quantity'])
        size = item_data.get('size')

        item_total = product.price * quantity
        total += item_total

        cart_items.append({
            'cart_key': cart_key,
            'product': product,
            'size': size,
            'quantity': quantity,
            'item_total': item_total,
        })

    if request.method == 'POST':
        form = CheckoutForm(request.POST)

        if form.is_valid():
            order = Order.objects.create(
                full_name=form.cleaned_data['full_name'],
                email=form.cleaned_data['email'],
                address=form.cleaned_data['address'],
                city=form.cleaned_data['city'],
                postcode=form.cleaned_data['postcode'],
                country=form.cleaned_data['country'],
                total_price=total,
            )

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    size=item['size'],
                    quantity=item['quantity'],
                    price=item['product'].price,
                )

            line_items = []

            for item in cart_items:
                product_name = item['product'].name

                if item['size']:
                    product_name = f"{product_name} - Size {item['size']}"

                line_items.append({
                    'price_data': {
                        'currency': 'gbp',
                        'product_data': {
                            'name': product_name,
                        },
                        'unit_amount': int(item['product'].price * 100),
                    },
                    'quantity': item['quantity'],
                })

            try:
                checkout_session = stripe.checkout.Session.create(
                    mode='payment',
                    line_items=line_items,
                    success_url=request.build_absolute_uri('/checkout/success/') + '?session_id={CHECKOUT_SESSION_ID}',
                    cancel_url=request.build_absolute_uri('/checkout/'),
                    customer_email=form.cleaned_data['email'],
                    metadata={
                        'order_id': str(order.id),
                        'customer_name': form.cleaned_data['full_name'],
                    },
                )

                order.stripe_session_id = checkout_session.id
                order.save()

                return redirect(checkout_session.url, code=303)

            except stripe.error.StripeError as e:
                order.delete()

                return render(request, 'store/checkout.html', {
                    'form': form,
                    'cart_items': cart_items,
                    'total': total,
                    'error': str(e),
                })

    else:
        form = CheckoutForm()

    return render(request, 'store/checkout.html', {
        'form': form,
        'cart_items': cart_items,
        'total': total,
    })


def checkout_success(request):
    request.session['cart'] = {}
    request.session.modified = True
    return render(request, 'store/checkout_success.html')


@csrf_exempt
def stripe_webhook(request):
    print("Webhook endpoint hit")

    try:
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        print("Event verified:", event['type'])

        if event['type'] == 'checkout.session.completed':
            print("Checkout session completed")

            session = event["data"]["object"]
            session_id = session["id"]
            metadata = session["metadata"]
            order_id = metadata["order_id"]

            print("Session ID:", session_id)
            print("Order ID from metadata:", order_id)

            if not order_id:
                print("No order_id found. Stripe test event or missing metadata.")
                return HttpResponse(status=200)

            try:
                order = Order.objects.get(id=order_id)
                print("Order found:", order.order_number)
            except Order.DoesNotExist:
                print("Order not found:", order_id)
                return HttpResponse(status=200)

            if not order.is_paid:
                order.is_paid = True
                order.save()
                print("Order marked as paid")
            else:
                print("Order was already marked as paid")

            try:
                send_order_confirmation_email(order, session)
                print("HTML email sent successfully to:", order.email)
            except Exception as e:
                print("Email sending failed:", str(e))

        return HttpResponse(status=200)

    except ValueError as e:
        print("Invalid payload:", str(e))
        return HttpResponse(status=400)

    except stripe.error.SignatureVerificationError as e:
        print("Invalid signature:", str(e))
        return HttpResponse(status=400)

    except Exception as e:
        print("Webhook unexpected error:", str(e))
        return HttpResponse(status=200)
    
def search(request):
    query = request.GET.get('q', '').strip()

    products = Product.objects.all().order_by('-created_at')

    if query:
        products = products.filter(name__icontains=query)

    return render(request, 'store/search.html', {
        'products': products,
        'query': query
    })


def toggle_favourite(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    favourites = request.session.get('favourites', [])

    if product_id in favourites:
        favourites.remove(product_id)
    else:
        favourites.append(product_id)

    request.session['favourites'] = favourites
    request.session.modified = True

    return redirect(request.META.get('HTTP_REFERER', 'home'))


def favourites(request):
    favourite_ids = request.session.get('favourites', [])
    products = Product.objects.filter(id__in=favourite_ids)

    return render(request, 'store/favourites.html', {
        'products': products
    })