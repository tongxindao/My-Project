#!/usr/bin/env python
# encoding: utf-8

from flask import render_template, flash, redirect, session, url_for, request, g
from flask_login import login_user, logout_user, current_user, login_required
from app import app, db, lm, oid
from .forms import LoginForm, EditForm, PostForm, SearchForm
from .models import User, Post, ROLE_USER, ROLE_ADMIN
from datetime import datetime
from config import POSTS_PER_PAGE, MAX_SEARCH_RESULTS
from emails import follower_notification

'''
以下三行代码可解决：UnicodeDecodeError: 'ascii' codec can't decode byte 0xe4 in position 0: ordinal not in range(128)
'''

import sys
reload(sys)
sys.setdefaultencoding('utf8')

@app.route('/', methods=['GET', 'POST'])
@app.route('/index', methods=['GET', 'POST'])
@app.route('/index/<int:page>', methods=['GET', 'POST'])
@login_required # 我们添加了login_required 装饰器。这确保了这页只被已经登录的用户看到。
def index(page = 1):
    form = PostForm()
    if form.validate_on_submit():
        post = Post(body = form.post.data, timestamp = datetime.utcnow(), author = g.user)
        db.session.add(post)
        db.session.commit()
        flash('您的信息现已推出！')
        return redirect(url_for('index')) # 避免用户在提交 blog 后不小心触发刷新的动作而导致插入重复的 blog。
    posts = g.user.followed_posts().paginate(page, POSTS_PER_PAGE, False) # User 类中的 followed_posts 方法返回一个 sqlalchemy 查询对象，该查询对象用于获取我们感兴趣的 blog。
    return render_template('index.html', 
        title = '主页',
        form = form,
        posts = posts)

@lm.user_loader
def load_user(id):
    return User.query.get(int(id))

@app.before_request
def before_request():
    g.user = current_user
    if g.user.is_authenticated:
        g.user_last_seen = datetime.utcnow()
        db.session.add(g.user)
        db.session.commit()
        g.search_form = SearchForm()
'''
全局变量 current_user 是被 Flask-Login 设置的，因此我们只需要把它赋给 g.user ，让访问起来更方便。有了这个，所有请求将会访问到登录用户，即使在模版里。
'''

# oid.loginhandle 告诉 Flask-OpenID 这是我们的登录视图函数
@app.route('/login', methods = ['GET', 'POST'])
@oid.loginhandler
def login():# Flask 中的 g 全局变量是一个在请求生命周期中用来存储和共享数据。
    if g.user is not None and g.user.is_authenticated:
    # flask_login 0.3之后将authenticated从函数更改为属性，把g.user.is_authenticated() 修改为g.user.is_authenticated就行了。
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        flash('登陆请求的OpenID = "' + form.openid.data + '", 记住我 = ' + str(form.remember_me.data))
        session['remember_me'] = form.remember_me.data
        return oid.try_login(form.openid.data, ask_for = ['nickname', 'email'])
    return render_template('login.html',
        title = '登陆',
        form = form,
        providers = app.config['OPENID_PROVIDERS'])

@oid.after_login
def after_login(resp):
    if resp.email is None or resp.email == "":
        flash('无效登陆，请重试！')
        return redirect(url_for('login'))
    user = User.query.filter_by(email = resp.email).first()
    if user is None:
        nickname = resp.nickname
        if nickname is None or nickname == "":
            nickname = resp.email.split('@')[0]
        nickname = User.make_unique_nickname(nickname)
        user = User(nickname = nickname, email = resp.email, role = ROLE_USER)
        db.session.add(user)
        db.session.commit()
        # 使用户关注他/她自己
        db.session.add(user.follow(user))
        db.session.commit()
    remember_me = False
    if 'remember_me' in session:
        remember_me = session['remember_me']
        session.pop('remember_me', None)
    login_user(user, remember = remember_me)
    return redirect(request.args.get('next') or url_for('index'))

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/user/<nickname>')
@app.route('/user/<nickname>/<int:page>')
@login_required
def user(nickname, page = 1):
    user = User.query.filter_by(nickname = nickname).first()
    if user is None:
        flash('昵称 ' + nickname + ' 未找到。')
        return redirect(url_for('index'))
    posts = user.posts.paginate(page, POSTS_PER_PAGE, False)
    return render_template('user.html',
        user = user,
        posts = posts)

@app.route('/edit', methods=['GET', 'POST'])
@login_required
def edit():
    form = EditForm(g.user.nickname)
    if form.validate_on_submit():
        g.user.nickname = form.nickname.data
        g.user.about_me = form.about_me.data
        db.session.add(g.user)
        db.session.commit()
        flash('你的更改已保存')
        return redirect(url_for('edit'))
    elif request.method != 'POST':
        form.nickname.data = g.user.nickname
        form.about_me.data = g.user.about_me
    return render_template('edit.html', form=form)

@app.errorhandler(404)
def internal_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

@app.route('/follow/<nickname>')
@login_required
def follow(nickname):
    user = User.query.filter_by(nickname = nickname).first()
    if user is None:
        flash('用户 %s 未找到。' % nickname)
        return redirect(url_for('index'))
    if user == g.user:
        flash('你不能关注自己！')
        return redirect(url_for('user', nickname=nickname))
    u = g.user.follow(user)
    if u is None:
        flash('不能关注 ' + nickname + '.')
        return redirect(url_for('user', nickname=nickname))
    db.session.add(u)
    db.session.commit()
    flash('目前你关注 ' + nickname + '!')
    follower_notification(user, g.user)
    return redirect(url_for('user', nickname = nickname))

@app.route('/unfollow/<nickname>')
@login_required
def unfollow(nickname):
    user = User.query.filter_by(nickname = nickname).first()
    if user is None:
        flash('用户 %s 未找到' % nickname)
        return redirect(url_for('index'))
    if user == g.user:
        flash('你不能取消关注自己！')
        return redirect(url_for('user', nickname = nickname))
    u = g.user.unfollow(user)
    if u is None:
        flash('不能取消关注 ' + nickname + '.')
        return redirect(url_for('user', nickname = nickname))
    db.session.add(u)
    db.session.commit()
    flash('你已停止关注 ' + nickname + '.')
    return redirect(url_for('user', nickname = nickname))

@app.route('/search', methods = ['POST'])
@login_required
def search():
    if not g.search_form.validate_on_submit():
        return redirect(url_for('index'))
    return redirect(url_for('search_results', query = g.search_form.search.data))

@app.route('/search_results/<query>')
@login_required
def search_results(query): # 不支持中文搜索
    results = Post.query.whoosh_search(query, MAX_SEARCH_RESULTS).all()
    return render_template('search_results.html',
        query = query,
        results = results)
