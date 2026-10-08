-- Dos roles: migrator (dueño del esquema; migraciones y pruebas) y app (ejecución).
-- Las contraseñas llegan como variables de psql desde init.sh; no hay secretos aquí.
CREATE ROLE migrator LOGIN CREATEDB PASSWORD :'migrator_pw';
CREATE ROLE app LOGIN PASSWORD :'app_pw';

ALTER DATABASE :"dbname" OWNER TO migrator;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO app;

ALTER SCHEMA public OWNER TO migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app;

-- Tablas y secuencias que cree migrator quedan usables por app (sin DDL).
-- Restricciones por tabla (p. ej. Evento sin UPDATE/DELETE) las aplica su migración.
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
