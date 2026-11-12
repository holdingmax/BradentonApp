"""
User accounts with hashed passwords (werkzeug.security): users.json en la
PC, la tabla config.usuarios con DATABASE_URL (config_store.py).

Pure data layer — permission checks (who's allowed to create/reset/delete
users) live in webapp.py's routes, not here.
"""

import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

import config_store

# Los usuarios se guardan enteros (leer todos, cambiar uno, guardar todos):
# con este candado dos cambios a la vez no se pisan (S5 de
# AUDITORIA_2026-11.md). El hash de la contraseña, que es lo lento, se calcula
# antes de tomarlo.
_USERS_LOCK = threading.RLock()

# Cada pedido de la página consulta el usuario de la sesión, y con PostgreSQL
# eso era una conexión y dos consultas por pedido (V5). Lo leído vale unos
# segundos; guardar desde la app lo descarta al instante, así un alta, una
# baja o un cambio de contraseña rigen enseguida. Solo un cambio hecho a mano
# en la base o en el archivo tarda esos segundos en verse.
_CACHE_SECONDS = 5
_cache = None  # (momento, usuarios)


def _read_users():
    users = config_store.read(config_store.USERS)
    if users is None:
        return {}
    if not isinstance(users, dict):
        raise config_store.Unreadable("Los usuarios guardados no tienen el formato esperado.")
    return users


def load_users():
    """Return {username: {"password_hash": str, "is_admin": bool}}.

    Sin usuarios guardados, {}. Si están pero no se pueden leer (archivo
    roto, base caída) da error y no deja entrar a nadie: nunca se toma como
    "no hay usuarios", que reabriría /primer-admin (A1 de AUDITORIA_2026-10.md).
    Lo devuelto es solo para leer; para cambiar algo, _read_users con el candado.
    """
    global _cache
    cached = _cache
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    users = _read_users()
    _cache = (now, users)
    return users


def save_users(users):
    """Persist the full user dict (temp file + os.replace en la PC; una transacción en Postgres)."""
    global _cache
    with _USERS_LOCK:
        try:
            config_store.write(config_store.USERS, users)
        finally:
            _cache = None


def has_users():
    # Siempre de lo guardado, sin la memoria: de esto depende /primer-admin.
    return bool(_read_users())


def get_user(username):
    return load_users().get(username)


def must_change_password(username):
    """True si este usuario tiene pendiente el cambio obligatorio de contraseña."""
    user = get_user(username)
    return bool(user and user.get("must_change_password", False))


def list_users():
    """Return [{"username": str, "is_admin": bool}, ...], sorted by username."""
    users = load_users()
    return [
        {"username": name, "is_admin": data.get("is_admin", False)}
        for name, data in sorted(users.items())
    ]


def verify_user(username, password):
    """Return the user dict on a correct username/password match, else None."""
    user = get_user(username)
    if user is None or not password:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def create_user(username, password, is_admin=False, must_change_password=True):
    username = username.strip()
    if not username:
        raise ValueError("El usuario no puede estar vacío.")
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    password_hash = generate_password_hash(password)
    with _USERS_LOCK:
        users = _read_users()
        if username in users:
            raise ValueError(f'Ya existe un usuario "{username}".')
        _add_user(users, username, password_hash, is_admin, must_change_password)


def _add_user(users, username, password_hash, is_admin, must_change_password):
    users[username] = {
        "password_hash": password_hash,
        "is_admin": bool(is_admin),
        # La contraseña la eligió el admin, no la persona -- se le exige
        # cambiarla ella misma la primera vez que entre (pedido explícito
        # del usuario, 2026-09-16). Se limpia sola en set_password() salvo
        # que se pida lo contrario (ver el reseteo manual de un admin). El
        # primer administrador elige la suya (webapp.primer_admin): False.
        "must_change_password": bool(must_change_password),
    }
    save_users(users)


def set_password(username, new_password, must_change_password=False):
    """Cambia la contraseña. `must_change_password=True` (reseteo hecho por
    un admin, no por la propia persona) vuelve a exigir el cambio obligatorio
    en el próximo login -- el cambio que hace el propio usuario (perfil)
    siempre lo deja en False."""
    if not new_password:
        raise ValueError("La contraseña no puede estar vacía.")
    password_hash = generate_password_hash(new_password)
    with _USERS_LOCK:
        users = _read_users()
        if username not in users:
            raise ValueError(f'No existe el usuario "{username}".')
        users[username]["password_hash"] = password_hash
        users[username]["must_change_password"] = bool(must_change_password)
        save_users(users)


def delete_user(username, current_username=None):
    """Remove a user — refuses to delete yourself or the last remaining admin."""
    with _USERS_LOCK:
        users = _read_users()
        if username not in users:
            raise ValueError(f'No existe el usuario "{username}".')
        if username == current_username:
            raise ValueError("No podés eliminar tu propia cuenta mientras estás conectado con ella.")
        other_admins = [
            name for name, data in users.items()
            if data.get("is_admin") and name != username
        ]
        if users[username].get("is_admin") and not other_admins:
            raise ValueError("No se puede eliminar el último administrador.")
        del users[username]
        save_users(users)
