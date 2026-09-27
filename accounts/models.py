from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model for the BOQ & Store app.

    It behaves exactly like Django's built-in User for now (same fields,
    same login), but because it's a separate class from day one, we can
    add the fields Section 2 of the spec needs later (per-project roles,
    phone number, etc.) without the very painful migration that's
    required if you switch AUTH_USER_MODEL after tables already exist.
    That later work is step 2 and is NOT done here.
    """

    class Meta:
        db_table = "accounts_user"
