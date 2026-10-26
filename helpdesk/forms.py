from django import forms
from .models import Ticket, TicketComment, Category, Tag


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
