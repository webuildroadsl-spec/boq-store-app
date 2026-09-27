from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """
    A minimal landing page behind login.

    It exists only so step 1 has something real to prove login works
    against: an anonymous visitor is bounced to the login page, and a
    logged-in user sees this page with their username and a logout link.
    Section 8's real dashboard is built much later (step 10).
    """
    return render(request, "accounts/home.html")
