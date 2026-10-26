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
    name        = models.CharField(max_length=50, unique=True)
    color       = models.CharField(max_length=7, default='#1a6b30')
    description = models.CharField(max_length=200, blank=True)
    use_count   = models.PositiveIntegerField(default=0)
    created_at  = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['name']

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
        (SOURCE_EMAIL,   'Email'),
        (SOURCE_PHONE,   'Phone'),
        (SOURCE_WALK_IN, 'Walk-In'),
        (SOURCE_WEB,     'Web Portal'),
        (SOURCE_VOIP,    'VoIP Call'),
        ('PORTAL',       'Requester Portal'),
        ('LINK',         'Quick Link'),
        ('RECURRING',    'Recurring Schedule'),
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
    # Who physically entered it (differs from `requester` for phone / walk-in / VoIP
    # callers who are not system users — `requester` is then left empty so demand
    # reports are not credited to the staff member who typed it in).
    logged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='tickets_logged')
    # Organisation snapshot taken when the ticket is raised, so reports stay correct
    # even if the person later moves office.
    requester_office     = models.CharField(max_length=100, blank=True, db_index=True)
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
        indexes = [
            models.Index(fields=['status'], name='ticket_status_idx'),
            models.Index(fields=['priority'], name='ticket_priority_idx'),
            models.Index(fields=['assigned_to'], name='ticket_assigned_idx'),
            models.Index(fields=['requester'], name='ticket_requester_idx'),
            models.Index(fields=['created_at'], name='ticket_created_idx'),
            models.Index(fields=['status','priority'], name='ticket_status_pri_idx'),
            models.Index(fields=['source'], name='ticket_source_idx'),
        ]

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
        indexes = [
            models.Index(fields=['is_published'], name='kb_published_idx'),
            models.Index(fields=['created_at'], name='kb_created_idx'),
        ]

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
    delivered = models.BooleanField(default=False)   # pushed to the browser (✓✓ grey)
    read_at   = models.DateTimeField(null=True, blank=True)   # actually seen by the person (✓✓ blue)
    conversation = models.CharField(max_length=40, blank=True, db_index=True)  # ticket_id — groups like a chat thread

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Notification for {self.user.username}"

    @property
    def is_read(self):
        return self.read_at is not None


# ── Knowledge Base ────────────────────────────────────────────────────────────

class KBArticle(models.Model):
    title        = models.CharField(max_length=200)
    slug         = models.SlugField(max_length=220, unique=True, blank=True)
    summary      = models.CharField(max_length=300, blank=True)
    content      = models.TextField()
    category     = models.ForeignKey('Category', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='kb_articles')
    tags         = models.CharField(max_length=200, blank=True,
                                    help_text='Comma-separated tags')
    views        = models.PositiveIntegerField(default=0)
    helpful      = models.PositiveIntegerField(default=0)
    not_helpful  = models.PositiveIntegerField(default=0)
    author       = models.ForeignKey(User, on_delete=models.SET_NULL,
                                     null=True, related_name='kb_articles')
    is_published = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-views', '-created_at']
        verbose_name = 'KB Article'

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.title)[:220]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


# ── IT Asset Registry ─────────────────────────────────────────────────────────

class Asset(models.Model):
    TYPE_CHOICES = [('DESKTOP','Desktop PC'),('LAPTOP','Laptop'),
                    ('PRINTER','Printer'),('PHONE','Phone/VoIP'),
                    ('TABLET','Tablet'),
                    ('NETWORK','Network Equipment'),('SERVER','Server'),
                    ('MONITOR','Monitor'),('OTHER','Other')]
    STATUS_CHOICES = [('ACTIVE','Active'),('REPAIR','Under Repair'),
                      ('RETIRED','Retired'),('MISSING','Missing')]

    asset_tag        = models.CharField(max_length=30, unique=True)
    name             = models.CharField(max_length=150)
    asset_type       = models.CharField(max_length=20, choices=TYPE_CHOICES, default='DESKTOP')
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ACTIVE')
    serial_number    = models.CharField(max_length=100, blank=True)
    # Phones and tablets carry up to three IMEI numbers (dual-SIM / eSIM). Kept exactly as recorded,
    # including any "/01" software-version suffix.
    imei             = models.CharField('IMEI 1', max_length=40, blank=True, db_index=True)
    imei2            = models.CharField('IMEI 2', max_length=40, blank=True)
    imei3            = models.CharField('IMEI 3', max_length=40, blank=True)
    manufacturer     = models.CharField(max_length=100, blank=True)
    model            = models.CharField(max_length=100, blank=True)
    department       = models.CharField(max_length=100, blank=True)
    section          = models.CharField(max_length=100, blank=True)
    location         = models.CharField(max_length=150, blank=True)
    assigned_to_user = models.ForeignKey(User, on_delete=models.SET_NULL,
                                          null=True, blank=True,
                                          related_name='assigned_assets')
    assigned_to_name = models.CharField(max_length=150, blank=True)
    purchase_date    = models.DateField(null=True, blank=True)
    purchase_cost    = models.DecimalField(max_digits=10, decimal_places=2,
                                           null=True, blank=True)
    warranty_expiry  = models.DateField(null=True, blank=True)
    notes            = models.TextField(blank=True)
    created_by       = models.ForeignKey(User, on_delete=models.SET_NULL,
                                          null=True, related_name='created_assets')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_tag']
        verbose_name = 'IT Asset'

    def __str__(self):
        return f'{self.asset_tag} — {self.name}'

    @property
    def imei_list(self):
        """The IMEI numbers that have been recorded, in order."""
        return [x for x in (self.imei, self.imei2, self.imei3) if x]

    @property
    def is_warranty_expired(self):
        from django.utils import timezone
        return bool(self.warranty_expiry and timezone.now().date() > self.warranty_expiry)


# ── Ticket Templates ──────────────────────────────────────────────────────────

class TicketTemplate(models.Model):
    name                 = models.CharField(max_length=100)
    description          = models.CharField(max_length=200, blank=True)
    icon                 = models.CharField(max_length=60, default='fas fa-ticket')
    color                = models.CharField(max_length=7, default='#2ea043',
                                            help_text='Hex colour used for the template tile')
    title_template       = models.CharField(max_length=255)
    description_template = models.TextField()
    category             = models.ForeignKey('Category', on_delete=models.SET_NULL,
                                              null=True, blank=True)
    priority             = models.CharField(max_length=10, default='MEDIUM')
    is_active            = models.BooleanField(default=True)
    use_count            = models.PositiveIntegerField(default=0)
    created_by           = models.ForeignKey(User, on_delete=models.SET_NULL,
                                              null=True, related_name='ticket_templates')
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-use_count', 'name']
        verbose_name = 'Ticket Template'

    def __str__(self):
        return self.name


# ── Recurring Tickets ─────────────────────────────────────────────────────────

class RecurringTicket(models.Model):
    FREQ_CHOICES = [('DAILY','Daily'),('WEEKLY','Weekly'),('MONTHLY','Monthly')]

    name                 = models.CharField(max_length=100)
    frequency            = models.CharField(max_length=10, choices=FREQ_CHOICES, default='MONTHLY')
    day_of_week          = models.PositiveSmallIntegerField(null=True, blank=True,
                                                            help_text='0=Mon…6=Sun')
    day_of_month         = models.PositiveSmallIntegerField(null=True, blank=True,
                                                             help_text='1-28')
    title_template       = models.CharField(max_length=255)
    description_template = models.TextField()
    category             = models.ForeignKey('Category', on_delete=models.SET_NULL,
                                              null=True, blank=True)
    priority             = models.CharField(max_length=10, default='MEDIUM')
    assigned_to          = models.ForeignKey(User, on_delete=models.SET_NULL,
                                              null=True, blank=True,
                                              related_name='recurring_assignments')
    is_active            = models.BooleanField(default=True)
    last_created_at      = models.DateTimeField(null=True, blank=True)
    next_due_at          = models.DateTimeField(null=True, blank=True)
    created_by           = models.ForeignKey(User, on_delete=models.SET_NULL,
                                              null=True, related_name='recurring_tickets')
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Recurring Ticket'

    def __str__(self):
        return f'{self.name} ({self.frequency})'


# ── Database Backup Registry ──────────────────────────────────────────────────

class DatabaseBackup(models.Model):
    """Tracks database backups created, restored, or deleted by staff."""
    ACTION_CREATE  = 'CREATE'
    ACTION_RESTORE = 'RESTORE'
    ACTION_DELETE  = 'DELETE'
    ACTION_CHOICES = [(ACTION_CREATE,'Created'),(ACTION_RESTORE,'Restored'),
                      (ACTION_DELETE,'Deleted')]

    STATUS_OK    = 'OK'
    STATUS_FAIL  = 'FAIL'
    STATUS_CHOICES = [(STATUS_OK,'Success'),(STATUS_FAIL,'Failed')]

    filename    = models.CharField(max_length=255)
    file_path   = models.CharField(max_length=500, blank=True)
    file_size   = models.PositiveIntegerField(default=0,
                                              help_text='Size in bytes')
    action      = models.CharField(max_length=10, choices=ACTION_CHOICES)
    status      = models.CharField(max_length=6, choices=STATUS_CHOICES, default=STATUS_OK)
    db_name     = models.CharField(max_length=100, blank=True,
                                   help_text='Database that was backed up')
    note        = models.TextField(blank=True)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                     null=True, related_name='db_backups')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Database Backup'

    def __str__(self):
        return f'{self.action} — {self.filename}'

    @property
    def file_size_display(self):
        if self.file_size < 1024:
            return f'{self.file_size} B'
        elif self.file_size < 1024**2:
            return f'{self.file_size/1024:.1f} KB'
        else:
            return f'{self.file_size/1024**2:.1f} MB'


# ── Ticket Approval / E-Signature ─────────────────────────────────────────────

class TicketApproval(models.Model):
    """
    An electronic sign-off (Approve / Reject) on a ticket. Replaces the old
    print-and-sign workflow.

    Records are append-only: a later decision supersedes an earlier one but the
    earlier one is kept as history. `document_hash` fingerprints the ticket's
    immutable content together with the decision and signature, so any later
    tampering with the stored record can be detected via `is_intact`.
    """
    DECISION_APPROVED = 'APPROVED'
    DECISION_REJECTED = 'REJECTED'
    DECISION_CHOICES = [
        (DECISION_APPROVED, 'Approved'),
        (DECISION_REJECTED, 'Rejected'),
    ]

    ticket          = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='approvals')
    decision        = models.CharField(max_length=10, choices=DECISION_CHOICES)
    reason          = models.TextField(blank=True, help_text='Required when rejecting')
    signer          = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='ticket_signatures')
    signer_name     = models.CharField(max_length=150)   # snapshot — survives user deletion/rename
    signer_title    = models.CharField(max_length=100, blank=True)
    signature_image = models.TextField(help_text='PNG data URL of the drawn signature')
    document_hash   = models.CharField(max_length=64, editable=False)
    ip_address      = models.GenericIPAddressField(null=True, blank=True)
    signed_at       = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-signed_at', '-id']
        indexes = [models.Index(fields=['ticket', 'signed_at'], name='approval_ticket_idx')]

    def __str__(self):
        return f'{self.ticket.ticket_id} {self.get_decision_display()} by {self.signer_name}'

    def compute_hash(self):
        import hashlib
        sig = hashlib.sha256((self.signature_image or '').encode()).hexdigest()
        parts = [
            self.ticket.ticket_id, self.ticket.title, self.ticket.description,
            self.decision, self.reason, str(self.signer_id or ''), self.signer_name,
            self.signed_at.isoformat(), sig,
        ]
        return hashlib.sha256('\x1f'.join(parts).encode()).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Signed approvals are immutable and cannot be modified.')
        self.document_hash = self.compute_hash()
        super().save(*args, **kwargs)

    @property
    def is_intact(self):
        return self.document_hash == self.compute_hash()



# ── Quick Request Links ───────────────────────────────────────────────────────

class QuickRequestLink(models.Model):
    """
    A personal, expiring link that lets a user raise a request WITHOUT a full
    login. The page behind it still demands the owner's password (and their
    authenticator / backup code when MFA is enabled) every time a request is
    submitted — the link only identifies the account, it never authenticates it.

    Only a SHA-256 hash of the token is stored, so a database leak does not leak
    usable links; the full URL is shown to the owner exactly once.
    Lives on the default database (like users), not the per-year ticket databases.
    """
    MAX_FAILED = 5          # consecutive wrong credentials before a temporary lock
    LOCK_MINUTES = 15
    MAX_LOCKS = 3           # lockouts before the link is disabled for good

    user          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quick_links')
    label         = models.CharField(max_length=100)
    token_hash    = models.CharField(max_length=64, unique=True, editable=False)
    token_hint    = models.CharField(max_length=8, editable=False)
    created_at    = models.DateTimeField(default=timezone.now)
    created_ip    = models.GenericIPAddressField(null=True, blank=True)
    expires_at    = models.DateTimeField()
    revoked_at    = models.DateTimeField(null=True, blank=True)
    revoked_note  = models.CharField(max_length=120, blank=True)
    last_used_at  = models.DateTimeField(null=True, blank=True)
    use_count     = models.PositiveIntegerField(default=0)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    lock_count    = models.PositiveSmallIntegerField(default=0)
    locked_until  = models.DateTimeField(null=True, blank=True)
    last_totp_step = models.BigIntegerField(default=0)   # one-time use of each authenticator code

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username}: {self.label} ({self.token_hint}…)'

    @staticmethod
    def hash_token(raw):
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, label, expires_at, ip=None):
        """Create a link; returns (link, raw_token). The raw token is never stored."""
        import secrets
        raw = secrets.token_urlsafe(32)
        link = cls.objects.create(user=user, label=label[:100], expires_at=expires_at,
                                  token_hash=cls.hash_token(raw), token_hint=raw[:6],
                                  created_ip=ip)
        return link, raw

    @classmethod
    def find(cls, raw):
        if not raw or len(raw) > 100:
            return None
        return cls.objects.select_related('user').filter(token_hash=cls.hash_token(raw)).first()

    @property
    def status(self):
        now = timezone.now()
        if self.revoked_at:
            return 'revoked'
        if self.expires_at <= now:
            return 'expired'
        if not self.user.is_active:
            return 'revoked'
        return 'active'

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def revoke(self, note=''):
        if not self.revoked_at:
            self.revoked_at = timezone.now()
            self.revoked_note = note[:120]
            self.save(update_fields=['revoked_at', 'revoked_note'])
