from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.db.models import Count, Q
from django.utils import timezone
from django.http import JsonResponse
from helpdesk.models import Ticket, AuditLog
from accounts.models import UserProfile, MFALog


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
    from helpdesk.models import DatabaseYear
    db_years = DatabaseYear.objects.filter(is_active=True).order_by('-year')
    form = AuthenticationForm()
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # ── DB year selection (store in session before login) ──────────────
            selected_year_id = request.POST.get('db_year','').strip()
            if selected_year_id:
                try:
                    yr = DatabaseYear.objects.get(pk=selected_year_id, is_active=True)
                    request.session['pending_db_year'] = yr.year
                    request.session['pending_db_name'] = yr.db_name
                except DatabaseYear.DoesNotExist: pass

            # ── MFA check — intercept BEFORE login() ─────────────────────────
            try:
                profile = user.profile
                if profile.mfa_enabled and (profile.face_mfa_enabled or profile.totp_enabled):
                    # Store user ID and next URL in session, do NOT log in yet
                    request.session['mfa_user_id'] = user.pk
                    request.session['mfa_next'] = request.POST.get('remember_me') and '/dashboard/' or request.GET.get('next', '/dashboard/')
                    request.session['mfa_remember_me'] = bool(request.POST.get('remember_me'))
                    return redirect('mfa_verify')
            except Exception:
                pass  # No profile yet — proceed with normal login

            # ── Complete login (no MFA or MFA not configured) ─────────────────
            login(request, user)
            request.session.set_expiry(
                60 * 60 * 24 * 30 if request.POST.get('remember_me') else 0
            )

            # Apply pending DB year
            if request.session.pop('pending_db_year', None):
                request.session['active_db_year'] = request.session.pop('pending_db_year', None) or yr.year
                request.session['active_db_name'] = request.session.pop('pending_db_name', '')
                try:
                    p = user.profile; p.preferred_db_year = request.session.get('active_db_year')
                    p.save(update_fields=['preferred_db_year'])
                except Exception: pass
            else:
                if selected_year_id:
                    try:
                        yr = DatabaseYear.objects.get(pk=selected_year_id, is_active=True)
                        request.session['active_db_year'] = yr.year
                        request.session['active_db_name'] = yr.db_name
                    except Exception: pass
                else:
                    request.session.pop('active_db_year', None)
                    request.session.pop('active_db_name', None)

            # Restore theme
            try: request.session['user_theme'] = user.profile.theme
            except Exception: request.session['user_theme'] = 'dark'

            # T&C check
            try:
                from accounts.models import TermsVersion
                tc = TermsVersion.objects.filter(is_current=True).first()
                if tc and (not user.profile.terms_accepted or user.profile.terms_version != tc.version):
                    return redirect('terms_accept')
            except Exception: pass

            return redirect(request.GET.get('next', '/dashboard/'))
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'accounts/login.html', {'form': form, 'db_years': db_years})


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


def _org_form_values(source):
    return {f: ' '.join(str(source.get(f, '')).split()) for f in ('office', 'department', 'section')}


def _org_choices_safe():
    try:
        from helpdesk.views import _org_choices
        return _org_choices()
    except Exception:
        return {'office': [], 'department': [], 'section': []}


@login_required
def user_create(request):
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can create new users.')
        return redirect('user_list')

    v = {'username': '', 'first_name': '', 'last_name': '', 'email': '', 'is_staff': False,
         'is_superuser': False, 'office': '', 'department': '', 'section': ''}
    errors = {}

    if request.method == 'POST':
        v.update(username=request.POST.get('username', '').strip(),
                 first_name=request.POST.get('first_name', '').strip(),
                 last_name=request.POST.get('last_name', '').strip(),
                 email=request.POST.get('email', '').strip(),
                 is_staff=request.POST.get('is_staff') == 'on',
                 is_superuser=request.POST.get('is_superuser') == 'on',
                 **_org_form_values(request.POST))
        password = request.POST.get('password', '').strip()
        confirm  = request.POST.get('confirm_password', '').strip()

        if not v['username']:
            errors['username'] = 'Username is required.'
        elif User.objects.filter(username__iexact=v['username']).exists():
            errors['username'] = f'Username "{v["username"]}" is already taken.'
        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 8:
            errors['password'] = 'Password must be at least 8 characters.'
        elif password != confirm:
            errors['confirm_password'] = 'Passwords do not match.'
        for f in ('office', 'department', 'section'):
            if len(v[f]) > 100:
                errors[f] = 'Too long — maximum 100 characters.'

        if not errors:
            is_staff = v['is_staff'] or v['is_superuser']       # superusers are automatically staff
            user = User.objects.create_user(
                username=v['username'], password=password,
                first_name=v['first_name'], last_name=v['last_name'],
                email=v['email'], is_staff=is_staff, is_superuser=v['is_superuser'])
            if any(v[f] for f in ('office', 'department', 'section')):
                prof, _ = UserProfile.objects.get_or_create(user=user)
                prof.office, prof.department, prof.section = v['office'], v['department'], v['section']
                prof.save(update_fields=['office', 'department', 'section'])
            role = 'Administrator' if v['is_superuser'] else ('IT Staff' if is_staff else 'User')
            log_action(request, AuditLog.ACTION_CREATE, user.pk,
                       user.username,
                       notes=f'Created by {request.user.username} as {role}')
            messages.success(request, f'User {v["username"]} created successfully as {role}.')
            return redirect('user_detail', user_id=user.pk)
        # Rejected: show the form again with everything typed (never the passwords)
        messages.error(request, 'User not created — please correct the highlighted field(s). Everything else has been kept.')

    return render(request, 'accounts/user_create.html',
                  {'v': v, 'errors': errors, 'org_choices': _org_choices_safe()})



# ═══════════════════════════════════════════════════════════════════════════════
# MFA — Face Recognition + TOTP Backup
# ═══════════════════════════════════════════════════════════════════════════════

def get_client_ip_mfa(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR','')


def log_mfa(user, method, result, request, confidence=None, note=''):
    from accounts.models import MFALog
    MFALog.objects.create(
        user=user, method=method, result=result,
        ip_address=get_client_ip_mfa(request),
        user_agent=request.META.get('HTTP_USER_AGENT','')[:300],
        confidence=confidence, note=note)


def mfa_verify(request):
    """
    Step 2 of login — shown after password is accepted.
    Tries face recognition first; if no camera/face, falls back to TOTP.
    Session key 'mfa_user_id' must be set by login_view before redirecting here.
    """
    user_id = request.session.get('mfa_user_id')
    if not user_id:
        return redirect('login')

    user = get_object_or_404(User, pk=user_id)
    profile = user.profile
    error   = None

    # Check if face is locked
    if profile.face_locked_until:
        from django.utils import timezone
        if timezone.now() < profile.face_locked_until:
            remaining = int((profile.face_locked_until - timezone.now()).total_seconds() / 60)
            error = f'Face recognition locked for {remaining} more minutes due to too many failures. Use your authenticator app instead.'

    if request.method == 'POST':
        method = request.POST.get('method','face')

        # ── Face Recognition ─────────────────────────────────────────────────
        if method == 'face':
            import json, math
            embedding_json = request.POST.get('embedding','')
            try:
                incoming = json.loads(embedding_json)
                stored   = json.loads(profile.face_embedding)

                # Cosine similarity between embeddings
                def cosine_sim(a, b):
                    dot   = sum(x*y for x,y in zip(a,b))
                    mag_a = math.sqrt(sum(x*x for x in a))
                    mag_b = math.sqrt(sum(x*x for x in b))
                    return dot / (mag_a * mag_b) if mag_a and mag_b else 0

                # Handle stored as list of embeddings (averaged from enrollment)
                if isinstance(stored[0], list):
                    similarities = [cosine_sim(incoming, s) for s in stored]
                    confidence = max(similarities)
                else:
                    confidence = cosine_sim(incoming, stored)

                THRESHOLD = 0.75  # Tune: higher = stricter

                if confidence >= THRESHOLD:
                    # PASS
                    profile.face_fail_count = 0
                    profile.face_locked_until = None
                    profile.save(update_fields=['face_fail_count','face_locked_until'])
                    log_mfa(user,'FACE','PASS',request,confidence=round(confidence,3))
                    _complete_login(request, user)
                    return _post_mfa_redirect(request, user)
                else:
                    # FAIL
                    profile.face_fail_count += 1
                    if profile.face_fail_count >= 5:
                        from datetime import timedelta
                        from django.utils import timezone
                        profile.face_locked_until = timezone.now() + timedelta(minutes=15)
                        log_mfa(user,'FACE','LOCKED',request,confidence=round(confidence,3),
                                note='Locked after 5 failures')
                    else:
                        log_mfa(user,'FACE','FAIL',request,confidence=round(confidence,3))
                    profile.save(update_fields=['face_fail_count','face_locked_until'])
                    error = f'Face not recognised (confidence {confidence:.1%}). Try better lighting, or use your authenticator app.'

            except Exception as e:
                error = 'Face verification failed — could not process embedding. Try again or use backup.'
                log_mfa(user,'FACE','FAIL',request,note=str(e)[:100])

        # ── TOTP ─────────────────────────────────────────────────────────────
        elif method == 'totp':
            import pyotp
            code = request.POST.get('totp_code','').strip().replace(' ','')
            totp = pyotp.TOTP(profile.totp_secret)
            if totp.verify(code, valid_window=1):
                log_mfa(user,'TOTP','PASS',request)
                _complete_login(request, user)
                return _post_mfa_redirect(request, user)
            else:
                error = 'Invalid authenticator code. Check time sync and try again.'
                log_mfa(user,'TOTP','FAIL',request)

        # ── Backup Code ───────────────────────────────────────────────────────
        elif method == 'backup':
            import json
            code    = request.POST.get('backup_code','').strip().upper()
            try:
                codes = json.loads(profile.totp_backup_codes or '[]')
                if code in codes:
                    codes.remove(code)
                    profile.totp_backup_codes = json.dumps(codes)
                    profile.save(update_fields=['totp_backup_codes'])
                    log_mfa(user,'BACKUP','PASS',request,
                            note=f'{len(codes)} backup codes remaining')
                    _complete_login(request, user)
                    return _post_mfa_redirect(request, user)
                else:
                    error = 'Invalid backup code.'
                    log_mfa(user,'BACKUP','FAIL',request)
            except Exception:
                error = 'Backup code error.'

    return render(request, 'accounts/mfa_verify.html', {
        'user':          user,
        'profile':       profile,
        'error':         error,
        'face_enabled':  profile.face_mfa_enabled and bool(profile.face_embedding),
        'totp_enabled':  profile.totp_enabled and bool(profile.totp_secret),
    })


def _post_mfa_redirect(request, user):
    """After successful MFA, check T&C then redirect to intended destination."""
    next_url = request.session.pop('mfa_next', '/dashboard/')
    try:
        from accounts.models import TermsVersion
        tc = TermsVersion.objects.filter(is_current=True).first()
        if tc and (not user.profile.terms_accepted or user.profile.terms_version != tc.version):
            return redirect('terms_accept')
    except Exception:
        pass
    return redirect(next_url)


def _complete_login(request, user):
    """Finalize login after successful MFA — apply session settings and T&C check."""
    from django.contrib.auth import login as auth_login
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    auth_login(request, user)

    # Apply remember me from pre-MFA session
    if request.session.pop('mfa_remember_me', False):
        request.session.set_expiry(60 * 60 * 24 * 30)

    # Apply pending DB year selection from login form
    pending_year = request.session.pop('pending_db_year', None)
    pending_name = request.session.pop('pending_db_name', None)
    if pending_year:
        request.session['active_db_year'] = pending_year
        request.session['active_db_name'] = pending_name or ''
        try:
            user.profile.preferred_db_year = pending_year
            user.profile.save(update_fields=['preferred_db_year'])
        except Exception:
            pass
    else:
        request.session.pop('active_db_year', None)
        request.session.pop('active_db_name', None)

    # Restore theme
    try:
        request.session['user_theme'] = user.profile.theme
    except Exception:
        request.session['user_theme'] = 'dark'

    # Clear MFA session keys
    request.session.pop('mfa_user_id', None)
    request.session.pop('mfa_remember_me', None)


def face_enroll(request):
    """
    Staff member captures 5 face images → embeddings stored in UserProfile.
    Admin can also enroll other users by passing ?user_id=<pk>.
    """
    if not request.user.is_authenticated:
        return redirect('login')

    target_id = request.GET.get('user_id', request.user.pk)
    if int(target_id) != request.user.pk and not request.user.is_superuser:
        messages.error(request, 'Only admins can enroll other users.')
        return redirect('profile')

    target = get_object_or_404(User, pk=target_id)
    profile = target.profile

    if request.method == 'POST' and request.content_type == 'application/json':
        import json, math
        data = json.loads(request.body)
        embeddings = data.get('embeddings', [])

        if len(embeddings) < 3:
            return JsonResponse({'ok': False, 'error': 'Need at least 3 face captures.'})

        # Average the embeddings for robustness
        n   = len(embeddings)
        dim = len(embeddings[0])
        avg = [sum(e[i] for e in embeddings) / n for i in range(dim)]

        from django.utils import timezone
        profile.face_embedding    = json.dumps(embeddings)  # store all for comparison
        profile.face_mfa_enabled  = True
        profile.mfa_enabled       = True
        profile.face_enrolled_at  = timezone.now()
        profile.face_fail_count   = 0
        profile.face_locked_until = None
        profile.save(update_fields=['face_embedding','face_mfa_enabled','mfa_enabled',
                                    'face_enrolled_at','face_fail_count','face_locked_until'])

        log_mfa(target, 'FACE', 'PASS', request, note=f'Enrollment: {n} captures stored')

        # Log in audit trail
        try:
            from helpdesk.models import AuditLog
            AuditLog.objects.create(
                user=request.user,
                action=AuditLog.ACTION_UPDATE,
                model_name='UserProfile',
                object_id=str(target.pk),
                object_repr=str(target.username),
                ip_address=get_client_ip_mfa(request),
                notes=f'Face MFA enrolled for {target.username}')
        except Exception:
            pass

        return JsonResponse({'ok': True, 'captures': n,
                             'message': f'Face enrolled with {n} captures. MFA is now active.'})

    # Setup TOTP if not already done
    if not profile.totp_secret:
        import pyotp, json
        profile.totp_secret = pyotp.random_base32()
        # Generate 8 backup codes
        import secrets
        codes = [secrets.token_hex(4).upper() for _ in range(8)]
        profile.totp_backup_codes = json.dumps(codes)
        profile.save(update_fields=['totp_secret','totp_backup_codes'])

    import pyotp
    totp_uri = pyotp.totp.TOTP(profile.totp_secret).provisioning_uri(
        name=target.email or target.username,
        issuer_name='IT HelpDesk — Beitbridge Municipality')

    return render(request, 'accounts/face_enroll.html', {
        'target':   target,
        'profile':  profile,
        'totp_uri': totp_uri,
        'is_self':  target == request.user,
    })


def mfa_settings(request):
    """User can toggle MFA on/off, reset face, reset TOTP."""
    if not request.user.is_authenticated:
        return redirect('login')

    profile = request.user.profile

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'disable_face':
            profile.face_mfa_enabled = False
            profile.face_embedding   = ''
            profile.face_enrolled_at = None
            profile.face_fail_count  = 0
            profile.face_locked_until = None
            if not profile.totp_enabled:
                profile.mfa_enabled = False
            profile.save()
            messages.success(request, 'Face recognition disabled.')
            log_mfa(request.user,'FACE','PASS',request,note='Face MFA disabled by user')

        elif action == 'disable_totp':
            profile.totp_enabled = False
            profile.totp_secret  = ''
            if not profile.face_mfa_enabled:
                profile.mfa_enabled = False
            profile.save()
            messages.success(request, 'Authenticator app disabled.')

        elif action == 'setup_totp':
            import pyotp
            totp_code = request.POST.get('totp_code','').strip()
            totp = pyotp.TOTP(profile.totp_secret)
            if totp.verify(totp_code, valid_window=1):
                profile.totp_enabled = True
                profile.mfa_enabled  = True
                profile.save(update_fields=['totp_enabled','mfa_enabled'])
                messages.success(request, 'Authenticator app set up successfully!')
                log_mfa(request.user,'TOTP','PASS',request,note='TOTP setup confirmed')
            else:
                messages.error(request, 'Invalid code. Check your authenticator app and try again.')

        elif action == 'regen_backup':
            import secrets, json
            codes = [secrets.token_hex(4).upper() for _ in range(8)]
            profile.totp_backup_codes = json.dumps(codes)
            profile.save(update_fields=['totp_backup_codes'])
            messages.success(request, 'New backup codes generated. Save them safely!')

        elif action == 'toggle_mfa':
            # Master on/off switch for MFA
            if profile.mfa_enabled:
                profile.mfa_enabled = False
                profile.save(update_fields=['mfa_enabled'])
                log_mfa(request.user, 'FACE', 'PASS', request,
                        note='MFA disabled by user')
                messages.success(request, 'MFA has been disabled. Your account now uses password only.')
            else:
                # Can only enable if at least one method is set up
                if profile.face_mfa_enabled or profile.totp_enabled:
                    profile.mfa_enabled = True
                    profile.save(update_fields=['mfa_enabled'])
                    log_mfa(request.user, 'FACE', 'PASS', request,
                            note='MFA enabled by user')
                    messages.success(request, 'MFA enabled. You will be asked to verify on next login.')
                else:
                    messages.error(request,
                        'Set up Face Recognition or an Authenticator App first, then enable MFA.')

        elif action == 'unlock_face' and request.user.is_superuser:
            target = get_object_or_404(User, pk=request.POST.get('user_id'))
            p = target.profile
            p.face_fail_count = 0
            p.face_locked_until = None
            p.save(update_fields=['face_fail_count','face_locked_until'])
            messages.success(request, f'Face lock cleared for {target.username}.')

        return redirect('mfa_settings')

    import json, pyotp
    # Always generate TOTP secret if none exists
    if not profile.totp_secret:
        profile.totp_secret = pyotp.random_base32()
        import secrets, json
        codes = [secrets.token_hex(4).upper() for _ in range(8)]
        profile.totp_backup_codes = json.dumps(codes)
        profile.save(update_fields=['totp_secret','totp_backup_codes'])

    # Always provide totp_uri so QR code renders (needed for setup AND re-scan)
    totp_uri = pyotp.totp.TOTP(profile.totp_secret).provisioning_uri(
        name=request.user.email or request.user.username,
        issuer_name='IT HelpDesk — Beitbridge Municipality')

    # Always generate backup codes if none exist (so they show on first visit)
    if not profile.totp_backup_codes:
        import secrets
        codes = [secrets.token_hex(4).upper() for _ in range(8)]
        profile.totp_backup_codes = json.dumps(codes)
        profile.save(update_fields=['totp_backup_codes'])

    backup_codes = []
    if profile.totp_backup_codes:
        try: backup_codes = json.loads(profile.totp_backup_codes)
        except: pass

    mfa_logs = MFALog.objects.filter(user=request.user).order_by('-created_at')[:20]

    return render(request, 'accounts/mfa_settings.html', {
        'profile':      profile,
        'totp_uri':     totp_uri,
        'backup_codes': backup_codes,
        'mfa_logs':     mfa_logs,
    })


def api_face_verify(request):
    """
    POST — verify a face embedding against the stored profile.
    Used by the mfa_verify page for real-time feedback.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    user_id = request.session.get('mfa_user_id')
    if not user_id:
        return JsonResponse({'ok': False, 'error': 'No MFA session'}, status=403)

    import json, math
    user    = get_object_or_404(User, pk=user_id)
    profile = user.profile
    data    = json.loads(request.body)
    incoming = data.get('embedding', [])

    if not incoming or not profile.face_embedding:
        return JsonResponse({'ok': False, 'confidence': 0, 'pass': False})

    stored = json.loads(profile.face_embedding)

    def cosine_sim(a, b):
        dot   = sum(x*y for x,y in zip(a,b))
        mag_a = math.sqrt(sum(x*x for x in a))
        mag_b = math.sqrt(sum(x*x for x in b))
        return dot / (mag_a * mag_b) if mag_a and mag_b else 0

    if isinstance(stored[0], list):
        confidence = max(cosine_sim(incoming, s) for s in stored)
    else:
        confidence = cosine_sim(incoming, stored)

    return JsonResponse({
        'ok':         True,
        'confidence': round(confidence, 4),
        'pass':       confidence >= 0.75,
        'threshold':  0.75,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Terms & Conditions
# ═══════════════════════════════════════════════════════════════════════════════

def terms_view(request):
    """Public T&C page — no login required."""
    from accounts.models import TermsVersion
    terms = TermsVersion.objects.filter(is_current=True).first()
    return render(request, 'accounts/terms.html', {'terms': terms})


@login_required
def terms_accept(request):
    """
    Shown after first login or when T&C version changes.
    User must accept before accessing the system.
    """
    from accounts.models import TermsVersion, UserTermsAcceptance
    terms = TermsVersion.objects.filter(is_current=True).first()

    if not terms:
        # No T&C configured yet — skip
        return redirect(request.GET.get('next', '/dashboard/'))

    profile = request.user.profile
    if profile.terms_accepted and profile.terms_version == terms.version:
        return redirect('/dashboard/')

    if request.method == 'POST':
        if request.POST.get('accept') == 'yes':
            profile.terms_accepted    = True
            profile.terms_accepted_at = timezone.now()
            profile.terms_version     = terms.version
            profile.save(update_fields=['terms_accepted','terms_accepted_at','terms_version'])

            UserTermsAcceptance.objects.create(
                user=request.user, version=terms,
                ip_address=get_client_ip_mfa(request),
                user_agent=request.META.get('HTTP_USER_AGENT','')[:300])

            messages.success(request, 'Terms accepted. Welcome to IT HelpDesk.')
            return redirect('/dashboard/')
        else:
            # Declined — log them out
            from django.contrib.auth import logout as auth_logout
            auth_logout(request)
            messages.warning(request,
                'You must accept the Terms & Conditions to use IT HelpDesk.')
            return redirect('login')

    return render(request, 'accounts/terms_accept.html', {'terms': terms})


@login_required
def terms_admin(request):
    """Admin page to publish new T&C versions."""
    if not request.user.is_superuser:
        messages.error(request, 'Only administrators can manage T&C versions.')
        return redirect('dashboard')

    from accounts.models import TermsVersion
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'publish':
            version = request.POST.get('version','').strip()
            content = request.POST.get('content','').strip()
            date    = request.POST.get('effective_date','')
            if not version or not content or not date:
                messages.error(request,'Version, content and effective date are required.')
            elif TermsVersion.objects.filter(version=version).exists():
                messages.error(request,f'Version {version} already exists.')
            else:
                terms = TermsVersion.objects.create(
                    version=version, content=content,
                    effective_date=date, is_current=True,
                    created_by=request.user)
                # Reset all users' acceptance so they re-accept on next login
                UserProfile.objects.all().update(
                    terms_accepted=False, terms_version='')
                messages.success(request,
                    f'T&C v{version} published. All users will be prompted to re-accept.')
                try:
                    from helpdesk.models import AuditLog
                    AuditLog.objects.create(
                        user=request.user, action=AuditLog.ACTION_CREATE,
                        model_name='TermsVersion', object_id=str(terms.pk),
                        object_repr=str(terms), notes='New T&C version published')
                except Exception: pass
        return redirect('terms_admin')

    versions   = TermsVersion.objects.all()
    from accounts.models import UserTermsAcceptance
    recent_acc = UserTermsAcceptance.objects.select_related(
        'user','version').order_by('-accepted_at')[:20]
    return render(request, 'accounts/terms_admin.html', {
        'versions': versions, 'recent_acceptances': recent_acc})


def user_edit(request, user_id):
    """Edit a user's details — staff can edit non-admins, admins can edit anyone."""
    if not request.user.is_staff:
        messages.error(request,'Access denied.')
        return redirect('dashboard')
    target = get_object_or_404(User, pk=user_id)
    if target.is_superuser and not request.user.is_superuser:
        messages.error(request,'Only administrators can edit admin accounts.')
        return redirect('user_list')
    if request.method == 'POST':
        target.first_name = request.POST.get('first_name','').strip()
        target.last_name  = request.POST.get('last_name','').strip()
        target.email      = request.POST.get('email','').strip()
        if request.user.is_superuser:
            target.is_staff     = request.POST.get('is_staff') == 'on'
            target.is_superuser = request.POST.get('is_superuser') == 'on'
        org_err = None
        if request.user.is_superuser:
            org = _org_form_values(request.POST)
            if any(len(x) > 100 for x in org.values()):
                org_err = 'Office, department and section must each be 100 characters or fewer.'
        if org_err:
            messages.error(request, org_err)
            return render(request, 'accounts/user_edit.html',
                          {'target': target, 'org': org, 'org_choices': _org_choices_safe()})
        target.save()
        if request.user.is_superuser:
            prof, _ = UserProfile.objects.get_or_create(user=target)
            prof.office, prof.department, prof.section = org['office'], org['department'], org['section']
            prof.save(update_fields=['office', 'department', 'section'])
        messages.success(request,f'User {target.username} updated.')
        return redirect('user_detail', user_id=user_id)
    prof = getattr(target, 'profile', None)
    return render(request,'accounts/user_edit.html',
                  {'target': target, 'org': {f: getattr(prof, f, '') for f in ('office', 'department', 'section')},
                   'org_choices': _org_choices_safe()})


def user_activity_redirect(request, user_id):
    """Redirect to helpdesk user_activity view."""
    return redirect(f'/dashboard/users/{user_id}/activity/')


def qr_image(request):
    """Serve the TOTP QR code as a PNG image — no JS library needed."""
    if not request.user.is_authenticated:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()
    import qrcode, io
    from django.http import HttpResponse
    import pyotp

    profile = request.user.profile
    if not profile.totp_secret:
        profile.totp_secret = pyotp.random_base32()
        profile.save(update_fields=['totp_secret'])

    uri = pyotp.totp.TOTP(profile.totp_secret).provisioning_uri(
        name=request.user.email or request.user.username,
        issuer_name='IT HelpDesk — Beitbridge Municipality')

    img = qrcode.make(uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return HttpResponse(buf.read(), content_type='image/png')
