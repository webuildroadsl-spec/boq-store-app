from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

# Register our custom User with the same admin screen Django ships for
# its built-in User, so /admin/ works exactly as expected out of the box.
admin.site.register(User, UserAdmin)
