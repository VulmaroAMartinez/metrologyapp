-- Privilegios de la base de datos ACTUAL (sin variables de psql). Lo ejecuta init.sh sobre la
-- base del sistema y las pruebas de roles sobre su base propia (metrologia_roles_test), de modo
-- que ambas tengan exactamente los mismos privilegios. Debe correr como dueño de la base
-- (migrator) o como superusuario.
DO $$
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app', current_database());
END
$$;

ALTER SCHEMA public OWNER TO migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app;

-- Tablas y secuencias que cree migrator quedan usables por app (sin DDL).
-- Restricciones por tabla (p. ej. Evento sin UPDATE/DELETE) las aplica su migración.
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
