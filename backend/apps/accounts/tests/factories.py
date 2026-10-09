import factory

from apps.accounts.models import Usuario

DEFAULT_PASSWORD = "contrasena-de-prueba-1"


class UsuarioFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Usuario
        skip_postgeneration_save = True

    nombre = factory.Faker("name")
    correo = factory.Sequence(lambda n: f"usuario{n}@empresa.test")
    rol = Usuario.Rol.TECNICO

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        obj.set_password(extracted or DEFAULT_PASSWORD)
        if create:
            obj.save()
