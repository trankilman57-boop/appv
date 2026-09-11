[app]
title = Ma Cave
package.name = macave
package.domain = org.yannick

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json

version = 0.1
requirements = python3,kivy

orientation = portrait
fullscreen = 0

# Add icon.filename = %(source.dir)s/icon.png once you have an icon ready

android.accept_sdk_license = True

android.permissions = INTERNET,CAMERA

# API/target Android versions (safe modern defaults as of 2026)
android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 0
