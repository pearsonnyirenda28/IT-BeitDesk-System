from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/',   views.login_view,  name='login'),
    path('logout/',  views.logout_view, name='logout'),
    path('profile/', views.profile,     name='profile'),

    # MFA
    path('mfa/verify/',        views.mfa_verify,     name='mfa_verify'),
    path('mfa/enroll/',        views.face_enroll,    name='face_enroll'),
    path('mfa/settings/',      views.mfa_settings,   name='mfa_settings'),
    path('mfa/qr/',            views.qr_image,       name='qr_image'),

    # Terms & Conditions
    path('terms/',             views.terms_view,     name='terms_view'),
    path('terms/accept/',      views.terms_accept,   name='terms_accept'),
    path('terms/admin/',       views.terms_admin,    name='terms_admin'),

    # User management
    path('users/',                        views.user_list,            name='user_list'),
    path('users/create/',                 views.user_create,          name='user_create'),
    path('users/<int:user_id>/',          views.user_detail,          name='user_detail'),
    path('users/<int:user_id>/edit/',     views.user_edit,            name='user_edit'),
    path('users/<int:user_id>/activity/', views.user_activity_redirect, name='user_activity'),
]
