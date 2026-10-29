"""Project package.

PyMySQL is used as the MySQL driver; Django expects the legacy MySQLdb API,
so register the alias before any database backend is imported.
"""
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    # PyMySQL is optional (Postgres/SQLite deployments).
    pass
