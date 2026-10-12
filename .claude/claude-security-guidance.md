# Seguridad de este repositorio
- Toda vista de la API debe declarar explícitamente su clase de permisos por rol.
- Los certificados PDF nunca se sirven como archivos públicos; solo por endpoint autenticado.
- No registrar contraseñas, tokens ni cookies en logs.
- Los eventos de la bitácora no se modifican ni se borran.