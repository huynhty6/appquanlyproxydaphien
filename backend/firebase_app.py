"""
Firebase Admin SDK initialization.
Firestore = NoSQL database, Firebase Auth = authentication.
"""

import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

from config import settings

_app = None


def init_firebase():
    global _app
    if _app is not None:
        return _app

    cred = credentials.Certificate(settings.FIREBASE_KEY_PATH)
    _app = firebase_admin.initialize_app(cred)
    return _app


def get_firestore():
    init_firebase()
    return firestore.client()


def get_firebase_auth():
    init_firebase()
    return firebase_auth


db = None


def get_db():
    global db
    if db is None:
        db = get_firestore()
    return db
