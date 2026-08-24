from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
import uuid


class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='fas fa-tag', help_text='FontAwesome class')
    color = models.CharField(max_length=7, default='#4A90D9')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Ticket(models.Model):
    PRIORITY_LOW = 'LOW'
    PRIORITY_MEDIUM = 'MEDIUM'
    PRIORITY_HIGH = 'HIGH'
    PRIORITY_CRITICAL = 'CRITICAL'

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_CRITICAL, 'Critical'),
    ]

    STATUS_OPEN = 'OPEN'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_PENDING = 'PENDING'
    STATUS_RESOLVED = 'RESOLVED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_REOPENED = 'REOPENED'

    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_PENDING, 'Pending'),
        (STATUS_RESOLVED, 'Resolved'),
        (STATUS_CLOSED, 'Closed'),
        (STATUS_REOPENED, 'Reopened'),
    ]

    SOURCE_EMAIL = 'EMAIL'
    SOURCE_PHONE = 'PHONE'
    SOURCE_WALK_IN = 'WALK_IN'
    SOURCE_WEB = 'WEB'
    SOURCE_VOIP = 'VOIP'

    SOURCE_CHOICES = [
        (SOURCE_EMAIL, 'Email'),
        (SOURCE_PHONE, 'Phone'),
        (SOURCE_WALK_IN, 'Walk-In'),
        (SOURCE_WEB, 'Web Portal'),
        (SOURCE_VOIP, 'VoIP Call'),
    ]

    ticket_id = models.CharField(max_length=20, unique=True, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='tickets')
    tags = models.ManyToManyField(Tag, blank=True)

    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_OPEN)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_WEB)

    # People
    requester = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='tickets_raised')
    requester_name = models.CharField(max_length=150, blank=True, help_text='If requester is not a system user')
    requester_email = models.EmailField(blank=True)
    requester_phone = models.CharField(max_length=30, blank=True)
    requester_department = models.CharField(max_length=100, blank=True,
                                            help_text='e.g. Revenue Department, Finance')
    requester_section    = models.CharField(max_length=100, blank=True,
                                            help_text='Sub-section within department, e.g. Accounts Payable')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets_assigned')

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    due_date = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # SLA
    sla_breach             = models.BooleanField(default=False)
    escalation_level       = models.PositiveSmallIntegerField(default=0)
    escalation_notified_at = models.DateTimeField(null=True, blank=True)
    resolution_time_minutes = models.PositiveIntegerField(null=True, blank=True)

    # Linked VoIP call
    voip_call = models.ForeignKey('voip.VoIPCall', on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.ticket_id:
            prefix = 'ND'
            from django.db.models import Max
            result = Ticket.objects.aggregate(max_id=Max('pk'))
            num = (result['max_id'] or 1000) + 1
            self.ticket_id = f'{prefix}{num:06d}'

        if self.status == self.STATUS_RESOLVED and not self.resolved_at:
            self.resolved_at = timezone.now()
            if self.created_at:
                delta = self.resolved_at - self.created_at
                self.resolution_time_minutes = int(delta.total_seconds() / 60)

        if self.status == self.STATUS_CLOSED and not self.closed_at:
            self.closed_at = timezone.now()

        super().save(*args, **kwargs)

    def __str__(self):
        return f'[{self.ticket_id}] {self.title}'

    @property
    def is_overdue(self):
        if self.due_date and self.status not in [self.STATUS_RESOLVED, self.STATUS_CLOSED]:
            return timezone.now() > self.due_date
        return False

    @property
    def priority_color(self):
        colors = {
            'LOW': '#28a745',
            'MEDIUM': '#ffc107',
            'HIGH': '#fd7e14',
            'CRITICAL': '#dc3545',
        }
        return colors.get(self.priority, '#6c757d')

    @property
    def status_color(self):
        colors = {
            'OPEN': '#17a2b8',
            'IN_PROGRESS': '#007bff',
            'PENDING': '#ffc107',
            'RESOLVED': '#28a745',
            'CLOSED': '#6c757d',
            'REOPENED': '#dc3545',
        }
        return colors.get(self.status, '#6c757d')


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal notes not visible to requester')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment on {self.ticket.ticket_id} by {self.author}'


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    file = models.FileField(upload_to='ticket_attachments/%Y/%m/')
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename


class AuditLog(models.Model):
    ACTION_CREATE = 'CREATE'
    ACTION_UPDATE = 'UPDATE'
    ACTION_DELETE = 'DELETE'
    ACTION_VIEW = 'VIEW'
    ACTION_LOGIN = 'LOGIN'
    ACTION_LOGOUT = 'LOGOUT'
    ACTION_ASSIGN = 'ASSIGN'
    ACTION_STATUS_CHANGE = 'STATUS_CHANGE'
    ACTION_COMMENT = 'COMMENT'
    ACTION_CALL_START = 'CALL_START'
    ACTION_CALL_END = 'CALL_END'

    ACTION_CHOICES = [
        (ACTION_CREATE, 'Created'),
        (ACTION_UPDATE, 'Updated'),
        (ACTION_DELETE, 'Deleted'),
        (ACTION_VIEW, 'Viewed'),
        (ACTION_LOGIN, 'Login'),
        (ACTION_LOGOUT, 'Logout'),
        (ACTION_ASSIGN, 'Assigned'),
        (ACTION_STATUS_CHANGE, 'Status Changed'),
        (ACTION_COMMENT, 'Comment Added'),
        (ACTION_CALL_START, 'Call Started'),
        (ACTION_CALL_END, 'Call Ended'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=50)
    object_id = models.CharField(max_length=50, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['model_name', 'object_id']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f'{self.timestamp} | {self.user} | {self.action} | {self.model_name}'


class KnowledgeBase(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    is_published = models.BooleanField(default=True)
    views = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


# ── Glossary ──────────────────────────────────────────────────────────────────

class GlossaryTerm(models.Model):
    term        = models.CharField(max_length=120, unique=True)
    definition  = models.TextField()
    category    = models.CharField(max_length=80, blank=True)
    example     = models.TextField(blank=True)
    added_by    = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, related_name='glossary_terms')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['term']
        verbose_name = 'Glossary Term'

    def __str__(self):
        return self.term


# ── Yearly Database Registry ──────────────────────────────────────────────────

class DatabaseYear(models.Model):
    year        = models.PositiveIntegerField(unique=True)
    db_name     = models.CharField(max_length=100)
    db_user     = models.CharField(max_length=100, blank=True)
    db_host     = models.CharField(max_length=100, blank=True)
    db_port     = models.CharField(max_length=10, default='5432')
    description = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)
    is_current  = models.BooleanField(default=False)
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, related_name='created_db_years')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year']
        verbose_name = 'Database Year'

    def __str__(self):
        return f'BeitDesk {self.year} ({self.db_name})'

    def save(self, *args, **kwargs):
        if self.is_current:
            DatabaseYear.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    @property
    def db_config(self):
        import os
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': self.db_name,
            'USER': self.db_user or os.environ.get('DB_USER', 'beitdesk_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': self.db_host or os.environ.get('DB_HOST', 'localhost'),
            'PORT': self.db_port or '5432',
        }


# ── Word Prediction / Autocomplete ───────────────────────────────────────────

class LearnedPhrase(models.Model):
    """
    Stores phrases extracted from real ticket/comment text.
    Used to power field-aware autocomplete suggestions as users type.
    Phrases are learned automatically when tickets and comments are saved.
    """
    FIELD_TITLE       = 'title'
    FIELD_DESCRIPTION = 'description'
    FIELD_COMMENT     = 'comment'
    FIELD_REQUESTER   = 'requester_name'

    FIELD_CHOICES = [
        (FIELD_TITLE,       'Ticket Title'),
        (FIELD_DESCRIPTION, 'Ticket Description'),
        (FIELD_COMMENT,     'Comment / Note'),
        (FIELD_REQUESTER,   'Requester Name'),
    ]

    phrase      = models.CharField(max_length=200, db_index=True)
    field_name  = models.CharField(max_length=30, choices=FIELD_CHOICES, db_index=True)
    category    = models.CharField(max_length=80, blank=True, db_index=True,
                                   help_text='Category slug if phrase is category-specific')
    use_count   = models.PositiveIntegerField(default=1)
    last_used   = models.DateTimeField(auto_now=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('phrase', 'field_name')]
        ordering        = ['-use_count', '-last_used']
        verbose_name    = 'Learned Phrase'

    def __str__(self):
        return f'[{self.field_name}] {self.phrase} (×{self.use_count})'

    @classmethod
    def learn(cls, text, field_name, category=''):
        """
        Extract n-grams from text and upsert into the learned phrases table.
        Learns single words (>=4 chars) and 2–4 word phrases.
        Filters out noise words and very short tokens.
        """
        if not text or len(text.strip()) < 3:
            return

        import re
        # Normalise — lowercase, strip punctuation except hyphens
        text = re.sub(r"[^\w\s\-']", ' ', text.lower())
        tokens = [t for t in text.split() if len(t) >= 3]

        # Stop words to ignore as standalone suggestions
        STOP = {
            'the','and','for','are','was','but','not','you','all','can','her',
            'was','one','our','out','day','get','has','him','his','how','its',
            'may','new','now','old','see','two','way','who','boy','did','its',
            'let','put','say','she','too','use','with','this','that','from',
            'have','they','been','said','each','which','their','time','will',
            'about','would','there','could','these','other','more','into',
            'some','than','then','them','well','also','when','what','your'
        }

        phrases_to_learn = set()

        # Single meaningful words (≥4 chars, not a stop word)
        for token in tokens:
            if len(token) >= 4 and token not in STOP:
                phrases_to_learn.add(token)

        # 2-gram and 3-gram phrases
        for n in (2, 3):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i:i + n]
                # Skip if all tokens are stop words
                if all(t in STOP for t in gram):
                    continue
                phrases_to_learn.add(' '.join(gram))

        # Upsert each phrase
        for phrase in phrases_to_learn:
            if len(phrase) > 200:
                continue
            obj, created = cls.objects.get_or_create(
                phrase=phrase,
                field_name=field_name,
                defaults={'category': category, 'use_count': 1},
            )
            if not created:
                obj.use_count += 1
                if category and not obj.category:
                    obj.category = category
                obj.save(update_fields=['use_count', 'last_used', 'category'])


class PendingNotification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pending_notifications")
    payload = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=False)
    class Meta:
        ordering = ["created_at"]
    def __str__(self):
        return f"Notification for {self.user.username}"
