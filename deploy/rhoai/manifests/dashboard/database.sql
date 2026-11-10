CREATE ROLE openshell_keycloak LOGIN PASSWORD :'keycloak_password';
CREATE DATABASE openshell_keycloak OWNER openshell_keycloak;
REVOKE ALL ON DATABASE openshell_keycloak FROM PUBLIC;
