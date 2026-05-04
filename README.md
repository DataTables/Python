# DataTables Python server-side libraries

This is a collection of Python libraries to provide easy server-side support for the [DataTables](https://datatables.net/) Javascript library - _the_ Javascript table library.

These libraries provide support for:

* Server-side processing - work with millions of rows
* Editor - CRUD UI for DataTables
* ColumnControl - Column search controls for DataTables
* SearchBuilder - Complex search logic UI

[SQLAlchemy Core](https://docs.sqlalchemy.org/en/20/core/) is used to provide the SQL database integration, allowing a wide range of databases to be supported. Furthermore, the library is framework-agnostic: use it with Flask, FastAPI, Django, or any other Python web framework that can pass a request body and a SQLAlchemy `Connection` or `Engine` to a route handler.


## Installation

```sh
pip install datatables_server
```

SQLAlchemy 2.x and Python date utilities are the two required dependencies for this library and are installed automatically with the above command. A database driver (e.g. `psycopg2`, `pymysql`, `aiosqlite`) must be installed separately.


## Quick Start

The following shows a Flask endpoint that will accept both DataTables and Editor requests:

```python
from flask import Flask, request, jsonify
from sqlalchemy import create_engine
from datatables_server import Editor, Field

app = Flask(__name__)
engine = create_engine("postgresql+psycopg2://user:pass@localhost/mydb")

@app.route("/api/staff", methods=["GET", "POST", "PUT", "DELETE"])
def staff():
    with engine.connect() as db:
        editor = (
            Editor(db, "staff", "id")
            .field(Field("first_name"))
            .field(Field("last_name"))
            .field(Field("position"))
            .field(Field("salary"))
        )
        response = editor.process(request.values.to_dict(flat=True))
        db.commit()
    return jsonify(response)
```

Similarly, if your table is readonly, the `DataTable` and `Column` classes can be used:

```python
from flask import Flask, request, jsonify
from sqlalchemy import create_engine
from datatables_server import DataTable, Column

app = Flask(__name__)
engine = create_engine("postgresql+psycopg2://user:pass@localhost/mydb")

@app.route("/api/staff", methods=["GET"])
def staff():
    with engine.connect() as db:
        table = (
            DataTable(db, "staff", "id")
            .column(Column("first_name"))
            .column(Column("last_name"))
            .column(Column("position"))
            .column(Column("salary"))
        )
        response = table.process()
        db.commit()
    return jsonify(response)
```

### Documentation

For full documentation, [please refer to the DataTables site](https://datatables.net/manual/python).


## License

MIT — see [LICENSE](LICENSE) for full text.
