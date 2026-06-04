from django.shortcuts import render, get_object_or_404, redirect
from django.conf import settings
from datetime import date, timedelta
from decimal import Decimal
import stripe

import json
from square.utils.webhooks_helper import verify_signature

from square import Square
from square.environment import SquareEnvironment
import uuid

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

    delivery_start = date.today() + timedelta(days=1)
    delivery_end = date.today() + timedelta(days=2)
    

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
        'delivery_start': delivery_start.strftime("%d %B"),
        'delivery_end': delivery_end.strftime("%d %B %Y"),
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
    products = Product.objects.all()

    gender = request.GET.get('gender')
    sort = request.GET.get('sort', 'new')
    favourites = request.session.get('favourites', [])

    if gender:
        products = products.filter(gender=gender)

    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'name':
        products = products.order_by('name')
    else:
        products = products.order_by('-created_at')

    return render(request, 'store/index.html', {
        'products': products,
        'current_sort': sort,
        'current_gender': gender,
        'favourites': favourites,
    })

def favourites(request):
    sort = request.GET.get('sort', 'new')

    favourite_ids = request.session.get('favourites', [])
    products = Product.objects.filter(id__in=favourite_ids)

    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'name':
        products = products.order_by('name')
    else:
        products = products.order_by('-created_at')

    return render(request, 'store/favourites.html', {
        'products': products,
        'favourites': favourite_ids,
        'current_sort': sort,
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

def faq(request):
    return render(request, 'store/faq.html')


def choose_payment_method(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.is_paid:
        return redirect('checkout_success')

    return render(request, 'store/choose_payment.html', {
        'order': order
    })

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

            return render(request, 'store/checkout.html', {
    'form': form,
    'cart_items': cart_items,
    'total': total,
    'show_payment_popup': True,
    'order': order,
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

def stripe_checkout(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.is_paid:
        return redirect('checkout_success')

    line_items = []

    for item in order.items.all():
        product_name = item.product.name

        if item.size:
            product_name = f"{product_name} - Size {item.size}"

        line_items.append({
            'price_data': {
                'currency': 'gbp',
                'product_data': {
                    'name': product_name,
                },
                'unit_amount': int(item.price * 100),
            },
            'quantity': item.quantity,
        })

    try:
        checkout_session = stripe.checkout.Session.create(
            mode='payment',
            line_items=line_items,
            success_url=request.build_absolute_uri('/checkout/success/') + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.build_absolute_uri(f'/checkout/payment/{order.id}/'),
            customer_email=order.email,
            metadata={
                'order_id': str(order.id),
                'customer_name': order.full_name,
            },
        )

        order.stripe_session_id = checkout_session.id
        order.save()

        return redirect(checkout_session.url, code=303)

    except stripe.error.StripeError as e:
        return render(request, 'store/choose_payment.html', {
            'order': order,
            'error': str(e),
        })
    

def square_checkout(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.is_paid:
        return redirect('checkout_success')

    environment = SquareEnvironment.SANDBOX
    if settings.SQUARE_ENVIRONMENT == "production":
        environment = SquareEnvironment.PRODUCTION

    client = Square(
        token=settings.SQUARE_ACCESS_TOKEN,
        environment=environment
    )

    line_items = []

    for item in order.items.all():
        product_name = item.product.name

        if item.size:
            product_name = f"{product_name} - Size {item.size}"

        line_items.append({
            "name": product_name,
            "quantity": str(item.quantity),
            "base_price_money": {
                "amount": int(item.price * 100),
                "currency": "GBP"
            }
        })

    result = client.checkout.payment_links.create(
        idempotency_key=str(uuid.uuid4()),
        order={
            "location_id": settings.SQUARE_LOCATION_ID,
            "line_items": line_items,
            "reference_id": str(order.id),
        },
        checkout_options={
            "redirect_url": request.build_absolute_uri("/checkout/success/")
        },
        pre_populated_data={
            "buyer_email": order.email
        }
    )

    return redirect(result.payment_link.url)

@csrf_exempt
def square_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    signature = request.headers.get("x-square-hmacsha256-signature")
    body = request.body.decode("utf-8")

    is_valid = verify_signature(
    request_body=body,
    signature_header=signature,
    signature_key=settings.SQUARE_WEBHOOK_SIGNATURE_KEY,
    notification_url=settings.SQUARE_WEBHOOK_URL,
)

    if not is_valid:
        return HttpResponse(status=403)

    event = json.loads(body)
    event_type = event.get("type")

    if event_type == "payment.created" or event_type == "payment.updated":
        payment = event.get("data", {}).get("object", {}).get("payment", {})

        order_id = payment.get("order_id")

        # Square order_id is not your Django order id,
        # so we need reference_id from the Square order.
        square_order_id = payment.get("order_id")

        if square_order_id:
            environment = SquareEnvironment.SANDBOX
            if settings.SQUARE_ENVIRONMENT == "production":
                environment = SquareEnvironment.PRODUCTION

            client = Square(
                token=settings.SQUARE_ACCESS_TOKEN,
                environment=environment
            )

            square_order = client.orders.get(order_id=square_order_id)

            reference_id = square_order.order.reference_id

            if reference_id:
                try:
                    order = Order.objects.get(id=reference_id)

                    if not order.is_paid:
                        order.is_paid = True
                        order.save()

                        try:
                            send_order_confirmation_email(order, {})
                        except Exception as e:
                            print("Square email failed:", str(e))

                except Order.DoesNotExist:
                    print("Order not found:", reference_id)

    return HttpResponse(status=200)