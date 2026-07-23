"""
BeitDesk Word Learning Signals
================================
Automatically extracts and stores phrases from ticket and comment text
whenever they are saved. This powers the autocomplete/word prediction
feature on the ticket form.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction


@receiver(post_save, sender='helpdesk.Ticket')
def learn_from_ticket(sender, instance, created, **kwargs):
    """Extract phrases from ticket title, description and requester name."""
    # Import here to avoid circular imports
    from helpdesk.models import LearnedPhrase

    # Determine category label for context-aware suggestions
    category = instance.category.name if instance.category else ''

    # Use on_commit so we don't slow down the save itself
    def _learn():
        try:
            # Learn from title — highest value field
            if instance.title:
                LearnedPhrase.learn(
                    instance.title,
                    LearnedPhrase.FIELD_TITLE,
                    category=category
                )

            # Learn from description
            if instance.description:
                LearnedPhrase.learn(
                    instance.description,
                    LearnedPhrase.FIELD_DESCRIPTION,
                    category=category
                )

            # Learn requester names (department names, people names)
            if instance.requester_name:
                LearnedPhrase.learn(
                    instance.requester_name,
                    LearnedPhrase.FIELD_REQUESTER,
                    category=''
                )

        except Exception:
            # Never let learning errors break normal ticket saving
            pass

    transaction.on_commit(_learn)


@receiver(post_save, sender='helpdesk.TicketComment')
def learn_from_comment(sender, instance, created, **kwargs):
    """Extract phrases from comment/note body text."""
    if not created:
        return  # Only learn from new comments, not edits

    from helpdesk.models import LearnedPhrase

    def _learn():
        try:
            if instance.body:
                # Get category from parent ticket
                category = ''
                try:
                    cat = instance.ticket.category
                    if cat:
                        category = cat.name
                except Exception:
                    pass

                LearnedPhrase.learn(
                    instance.body,
                    LearnedPhrase.FIELD_COMMENT,
                    category=category
                )
        except Exception:
            pass

    transaction.on_commit(_learn)
