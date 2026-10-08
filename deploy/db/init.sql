-- Una sola vez, como superusuario, al crear el volumen: los dos roles del clúster y el dueño
-- de la base. Los privilegios por base de datos están en privileges.sql.
-- Las contraseñas llegan como variables de psql desde init.sh; no hay secretos aquí.
--   migrator: dueño del esquema; migraciones y pruebas.
--   app:      ejecución; sin privilegios de esquema.
CREATE ROLE migrator LOGIN CREATEDB PASSWORD :'migrator_pw';
CREATE ROLE app LOGIN PASSWORD :'app_pw';

ALTER DATABASE :"dbname" OWNER TO migrator;
