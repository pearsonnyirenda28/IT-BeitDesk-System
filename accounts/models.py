from django.db import models
# IT HelpDesk uses Django's built-in User model.
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
    # Face MFA
    face_mfa_enabled     = models.BooleanField(default=False)
    face_embedding       = models.TextField(blank=True,
                           help_text='JSON list of averaged face embeddings from enrollment')
    face_enrolled_at     = models.DateTimeField(null=True, blank=True)
    face_fail_count      = models.PositiveSmallIntegerField(default=0,
                           help_text='Consecutive failed face verifications')
    face_locked_until    = models.DateTimeField(null=True, blank=True,
                           help_text='Account locked after too many face failures')
    # TOTP backup MFA
    totp_secret          = models.CharField(max_length=64, blank=True,
                           help_text='Base32 TOTP secret key')
    totp_enabled         = models.BooleanField(default=False)
    totp_backup_codes    = models.TextField(blank=True,
                           help_text='JSON list of one-time backup codes')
    # MFA state
    mfa_enabled          = models.BooleanField(default=False,
                           help_text='Master switch — both face and TOTP off if False')
    # Terms & Conditions
    terms_accepted       = models.BooleanField(default=False,
                           help_text='User has accepted the system T&C')
    terms_accepted_at    = models.DateTimeField(null=True, blank=True)
    terms_version        = models.CharField(max_length=10, blank=True,
                           help_text='Version of T&C accepted')
    # Organisation — used to pick users per office and for request-demand reports
    office               = models.CharField(max_length=100, blank=True, db_index=True)
    department           = models.CharField(max_length=100, blank=True, db_index=True)
    section              = models.CharField(max_length=100, blank=True, db_index=True)
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


class MFALog(models.Model):
    """Audit trail for every MFA attempt — pass, fail, method, IP, timestamp."""
    METHOD_FACE   = 'FACE'
    METHOD_TOTP   = 'TOTP'
    METHOD_BACKUP = 'BACKUP'
    METHOD_CHOICES = [
        (METHOD_FACE,'Face Recognition'),
        (METHOD_TOTP,'Authenticator App'),
        (METHOD_BACKUP,'Backup Code'),
    ]
    RESULT_PASS    = 'PASS'
    RESULT_FAIL    = 'FAIL'
    RESULT_LOCKED  = 'LOCKED'
    RESULT_CHOICES = [
        (RESULT_PASS,'Passed'),(RESULT_FAIL,'Failed'),(RESULT_LOCKED,'Account Locked'),
    ]
    user       = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name='mfa_logs')
    method     = models.CharField(max_length=10, choices=METHOD_CHOICES)
    result     = models.CharField(max_length=10, choices=RESULT_CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    confidence = models.FloatField(null=True, blank=True,
                                   help_text='Face match confidence 0-1')
    note       = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'MFA Log'

    def __str__(self):
        return f'{self.user.username} {self.method} {self.result} @ {self.created_at:%Y-%m-%d %H:%M}'


class TermsVersion(models.Model):
    """
    Stores each published version of the system Terms & Conditions.
    When a new version is published, all users must re-accept on next login.
    """
    version     = models.CharField(max_length=10, unique=True,
                                   help_text='e.g. 1.0, 1.1, 2.0')
    title       = models.CharField(max_length=200,
                                   default='IT HelpDesk System Terms & Conditions')
    content     = models.TextField(help_text='Full T&C text (plain text or HTML)')
    is_current  = models.BooleanField(default=False,
                                      help_text='Only one version can be current')
    effective_date = models.DateField()
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, related_name='terms_versions')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Terms Version'

    def save(self, *args, **kwargs):
        if self.is_current:
            # Ensure only one current version
            TermsVersion.objects.filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'T&C v{self.version} ({"current" if self.is_current else "archived"})'


class UserTermsAcceptance(models.Model):
    """Audit trail of every T&C acceptance — immutable record."""
    user        = models.ForeignKey(User, on_delete=models.CASCADE,
                                    related_name='terms_acceptances')
    version     = models.ForeignKey(TermsVersion, on_delete=models.CASCADE)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    user_agent  = models.CharField(max_length=300, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-accepted_at']
        verbose_name = 'T&C Acceptance Record'

    def __str__(self):
        return f'{self.user.username} accepted v{self.version.version} at {self.accepted_at:%Y-%m-%d}'
