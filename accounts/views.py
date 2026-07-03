from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.db.models import Count, Q
from django.utils import timezone
from helpdesk.models import Ticket, AuditLog


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


def log_action(request, action, object_id, object_repr, notes=''):
    AuditLog.objects.create(
        user=request.user,
        action=action,
        model_name='User',
        object_id=str(object_id),
        object_repr=object_repr,
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        notes=notes,
    )


# ── Login / Logout ────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = AuthenticationForm()
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            request.session.set_expiry(
                60 * 60 * 24 * 30 if request.POST.get('remember_me') else 0
            )
            return redirect(request.GET.get('next', '/dashboard/'))
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'accounts/login.html', {'form': form})


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


# ── Profile ───────────────────────────────────────────────────────────────────

@login_required
def profile(request):
    if request.method == 'POST' and request.POST.get('action') == 'change_password':
        form = SetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            log_action(request, AuditLog.ACTION_UPDATE, request.user.pk,
                       request.user.username, notes='Password changed by user')
            messages.success(request, 'Password changed successfully.')
            return redirect('profile')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = SetPasswordForm(request.user)

    tickets_assigned = Ticket.objects.filter(assigned_to=request.user)
    calls_count = 0
    try:
        from voip.models import VoIPCall
        calls_count = VoIPCall.objects.filter(answered_by=request.user).count()
    except Exception:
        pass

    return render(request, 'accounts/profile.html', {
        'profile_user': request.user,
        'form': form,
        'tickets_assigned': tickets_assigned.count(),
        'tickets_resolved': tickets_assigned.filter(
            status__in=['RESOLVED', 'CLOSED']).count(),
        'calls_count': calls_count,
    })


# ── User Management (staff only) ─────────────────────────────────────────────

@login_required
def user_list(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    users = User.objects.annotate(
        open_tickets=Count('tickets_assigned',
                           filter=Q(tickets_assigned__status__in=['OPEN', 'IN_PROGRESS'])),
    ).order_by('-is_active', '-is_staff', 'username')

    search = request.GET.get('search', '')
    if search:
        users = users.filter(
            Q(username__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search)
        )

    return render(request, 'accounts/user_list.html', {
        'users': users,
        'search': search,
    })


@login_required
def user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)

    # Anyone can manage their own profile; viewing someone else's requires staff
    is_own_profile = (target == request.user)
    if not is_own_profile and not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    # Staff cannot manage admin accounts OR other staff accounts
    protected_target = (
        request.user.is_staff
        and not request.user.is_superuser
        and (target.is_superuser or (target.is_staff and not is_own_profile))
    )
    if protected_target and request.method == 'POST':
        messages.error(request, 'IT staff cannot manage administrator or other staff accounts.')
        return redirect('user_detail', user_id=user_id)

    # Determines which management controls the template shows
    # Superusers can manage anyone; staff can only manage non-admins (not self for status)
    can_manage = (
        request.user.is_superuser or
        (request.user.is_staff
         and not request.user.is_superuser
         and not target.is_superuser
         and not target.is_staff       # staff cannot manage other staff
         and not is_own_profile)
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        # ── Toggle active status ──────────────────────────────────────────────
        if action == 'toggle_active':
            if target == request.user:
                messages.error(request, 'You cannot deactivate your own account.')
            else:
                target.is_active = not target.is_active
                target.save()
                status = 'activated' if target.is_active else 'deactivated'
                log_action(request, AuditLog.ACTION_UPDATE, target.pk,
                           target.username, notes=f'Account {status}')
                messages.success(
                    request,
                    f'User {target.username} has been {status}.'
                )
            return redirect('user_detail', user_id=user_id)

        # ── Toggle staff status ───────────────────────────────────────────────
        elif action == 'toggle_staff':
            if not request.user.is_superuser:
                messages.error(request, 'Only superusers can change staff status.')
            elif target == request.user:
                messages.error(request, 'You cannot change your own staff status.')
            else:
                target.is_staff = not target.is_staff
                target.save()
                role = 'IT Staff' if target.is_staff else 'regular user'
                log_action(request, AuditLog.ACTION_UPDATE, target.pk,
                           target.username, notes=f'Role changed to {role}')
                messages.success(
                    request,
                    f'{target.username} is now a {role}.'
                )
            return redirect('user_detail', user_id=user_id)

        # ── Toggle admin (superuser) status ───────────────────────────────────
        elif action == 'toggle_admin':
            if not request.user.is_superuser:
                messages.error(request, 'Only administrators can promote or demote other administrators.')
            elif target == request.user:
                messages.error(request, 'You cannot change your own administrator status.')
            else:
                target.is_superuser = not target.is_superuser
                # Admins must also be staff
                if target.is_superuser:
                    target.is_staff = True
                target.save()
                role = 'Administrator' if target.is_superuser else 'IT Staff'
                log_action(request, AuditLog.ACTION_UPDATE, target.pk,
                           target.username, notes=f'Admin status changed to {role}')
                messages.success(
                    request,
                    f'{target.username} is now a {role}.'
                )
            return redirect('user_detail', user_id=user_id)

        # ── Reset password ────────────────────────────────────────────────────
        elif action == 'reset_password':
            if not request.user.is_superuser and target != request.user:
                messages.error(request, 'Only superusers can reset other users\' passwords.')
                return redirect('user_detail', user_id=user_id)

            new_pw = request.POST.get('new_password', '').strip()
            confirm_pw = request.POST.get('confirm_password', '').strip()

            if not new_pw:
                messages.error(request, 'Password cannot be empty.')
            elif len(new_pw) < 8:
                messages.error(request, 'Password must be at least 8 characters.')
            elif new_pw != confirm_pw:
                messages.error(request, 'Passwords do not match.')
            else:
                target.set_password(new_pw)
                target.save()
                if target == request.user:
                    update_session_auth_hash(request, target)
                log_action(request, AuditLog.ACTION_UPDATE, target.pk,
                           target.username,
                           notes=f'Password reset by {request.user.username}')
                messages.success(
                    request,
                    f'Password for {target.username} has been reset successfully.'
                )
            return redirect('user_detail', user_id=user_id)

        # ── Update profile info ───────────────────────────────────────────────
        elif action == 'update_profile':
            target.first_name = request.POST.get('first_name', '').strip()
            target.last_name  = request.POST.get('last_name', '').strip()
            target.email      = request.POST.get('email', '').strip()
            target.save()
            log_action(request, AuditLog.ACTION_UPDATE, target.pk,
                       target.username, notes='Profile info updated')
            messages.success(request, 'Profile updated.')
            return redirect('user_detail', user_id=user_id)

    # ticket stats for this user
    tickets_qs = Ticket.objects.filter(assigned_to=target)
    calls_count = 0
    try:
        from voip.models import VoIPCall
        calls_count = VoIPCall.objects.filter(answered_by=target).count()
    except Exception:
        pass

    audit_logs = AuditLog.objects.filter(
        model_name='User', object_id=str(target.pk)
    ).order_by('-timestamp')[:20]

    return render(request, 'accounts/user_detail.html', {
        'target': target,
        'tickets_total':    tickets_qs.count(),
        'tickets_resolved': tickets_qs.filter(status__in=['RESOLVED', 'CLOSED']).count(),
        'tickets_open':     tickets_qs.filter(status__in=['OPEN', 'IN_PROGRESS']).count(),
        'calls_count':      calls_count,
        'audit_logs':       audit_logs,
        'can_manage':       can_manage,
        'is_own_profile':   is_own_profile,
    })


@login_required
def user_create(request):
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can create new users.')
        return redirect('user_list')

    if request.method == 'POST':
        username     = request.POST.get('username', '').strip()
        first_name   = request.POST.get('first_name', '').strip()
        last_name    = request.POST.get('last_name', '').strip()
        email        = request.POST.get('email', '').strip()
        password     = request.POST.get('password', '').strip()
        confirm      = request.POST.get('confirm_password', '').strip()
        is_staff     = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'

        errors = []
        if not username:
            errors.append('Username is required.')
        elif User.objects.filter(username=username).exists():
            errors.append(f'Username "{username}" is already taken.')
        if not password:
            errors.append('Password is required.')
        elif len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        elif password != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # Superusers are automatically also staff
            if is_superuser:
                is_staff = True
            user = User.objects.create_user(
                username=username, password=password,
                first_name=first_name, last_name=last_name,
                email=email, is_staff=is_staff,
                is_superuser=is_superuser,
            )
            role = 'Administrator' if is_superuser else ('IT Staff' if is_staff else 'User')
            log_action(request, AuditLog.ACTION_CREATE, user.pk,
                       user.username,
                       notes=f'Created by {request.user.username} as {role}')
            messages.success(request, f'User {username} created successfully as {role}.')
            return redirect('user_detail', user_id=user.pk)

    return render(request, 'accounts/user_create.html')
