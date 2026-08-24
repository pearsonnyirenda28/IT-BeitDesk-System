from django.db import models
# NexusDesk uses Django's built-in User model.
# Extend here if you need a custom UserProfile in the future.
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    THEME_DARK  = 'dark'
    THEME_LIGHT = 'light'
    THEME_CHOICES = [(THEME_DARK,'Dark'),(THEME_LIGHT,'Light')]
    user                 = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    theme                = models.CharField(max_length=10, choices=THEME_CHOICES, default=THEME_DARK)
    notify_assigned      = models.BooleanField(default=True)
    notify_status_change = models.BooleanField(default=True)
    notify_new_comment   = models.BooleanField(default=False)
    push_subscription    = models.TextField(blank=True)
    push_enabled         = models.BooleanField(default=False)
    preferred_db_year    = models.PositiveIntegerField(null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)
    def __str__(self): return f'{self.user.username} profile'

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created: UserProfile.objects.get_or_create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    try: instance.profile.save()
    except UserProfile.DoesNotExist: UserProfile.objects.create(user=instance)
