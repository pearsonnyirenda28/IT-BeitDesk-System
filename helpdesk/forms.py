from django import forms
from .models import Ticket, TicketComment, Category, Tag


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = [
            'title', 'description', 'category', 'priority', 'status', 'source',
            'requester_name', 'requester_email', 'requester_phone',
            'requester_department', 'requester_section',
            'assigned_to', 'due_date', 'tags',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brief issue title'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'Describe the issue in detail...'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'requester_name': forms.TextInput(attrs={'class': 'form-control'}),
            'requester_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'requester_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'requester_department': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Revenue Department'}),
            'requester_section':    forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Accounts Payable, Licensing'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
            'due_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }


class TicketCommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ['body', 'is_internal']
        widgets = {
            'body': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Add a comment or update...'}),
            'is_internal': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TicketFilterForm(forms.Form):
    search = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Search tickets...'}))
    status = forms.ChoiceField(required=False, choices=[('', 'All Statuses')] + Ticket.STATUS_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    priority = forms.ChoiceField(required=False, choices=[('', 'All Priorities')] + Ticket.PRIORITY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    category = forms.ModelChoiceField(required=False, queryset=Category.objects.all(), empty_label='All Categories', widget=forms.Select(attrs={'class': 'form-select'}))
