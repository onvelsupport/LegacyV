from django import forms


class CheckoutForm(forms.Form):
    full_name = forms.CharField(max_length=200)
    email = forms.EmailField()
    address = forms.CharField(max_length=255)
    city = forms.CharField(max_length=100)
    postcode = forms.CharField(max_length=20)
    country = forms.CharField(max_length=100)