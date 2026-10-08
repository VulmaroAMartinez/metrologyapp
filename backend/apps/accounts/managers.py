from django.contrib.auth.base_user import BaseUserManager


class UsuarioManager(BaseUserManager):
    """Creación de bajo nivel. Las reglas de negocio (quién puede crear, eventos,
    validación de contraseña) viven en `services`."""

    use_in_migrations = True

    def create_user(self, correo, nombre, rol, password=None, **extra_fields):
        if not correo:
            raise ValueError("El correo es obligatorio.")
        if not nombre:
            raise ValueError("El nombre es obligatorio.")
        if rol not in self.model.Rol.values:
            raise ValueError(f"Rol inválido: {rol!r}.")
        usuario = self.model(
            correo=self.normalize_email(correo).lower(),
            nombre=nombre,
            rol=rol,
            **extra_fields,
        )
        if password is None:
            usuario.set_unusable_password()
        else:
            usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, *args, **kwargs):
        raise TypeError("No hay superusuarios: el administrador es un Usuario con rol ADMIN.")
