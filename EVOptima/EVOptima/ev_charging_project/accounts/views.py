from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required


def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next') or '/'
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password')
    else:
        form = AuthenticationForm(request)
    return render(request, 'accounts/login.html', {'form': form})


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('/dashboard/')
        else:
            messages.error(request, 'Please correct the errors below')
    else:
        form = UserCreationForm()
    return render(request, 'accounts/register.html', {'form': form})


def root_redirect(request):
    # If user is authenticated, go to dashboard, else go to login
    if request.user.is_authenticated:
        return redirect('/dashboard/')
    return redirect('/accounts/login/?next=/dashboard/')
