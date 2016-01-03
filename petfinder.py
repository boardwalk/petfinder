#!/usr/bin/env python3
import flask
import json
import os
import requests
import sqlite3

app = flask.Flask(__name__)
DATABASE_PATH = 'petfinder.db'
with open("petfinder.json") as f:
    CONFIG = json.load(f)

# petfinder spits out this gross "xml as json" stuff
# this cleans it up a bit
# for tags with text and attributes, only the text is retained
def demangle(value):
    if isinstance(value, dict):
        value = {k: demangle(v) for k, v in value.items()}
        if not value:
            return ''
        if '$t' in value:
            return value['$t']
        for k in ('pet', 'breed', 'photo', 'option'):
            if k in value:
                value = value[k]
                if not isinstance(value, list):
                    value = [value]
                return value
        return value
   
    if isinstance(value, list):
        return [demangle(v) for v in value]

    return value

def get_conn():
    if hasattr(flask.g, '_conn'):
        return flask.g._conn
    conn = flask.g._conn = sqlite3.connect(DATABASE_PATH)
    if not conn.execute('SELECT * FROM sqlite_master').fetchone():
        conn.executescript('''
            CREATE TABLE pet (
                pet_id INTEGER PRIMARY KEY,
                blob TEXT NOT NULL CHECK (json_valid(blob) = 1),
                created TEXT NOT NULL DEFAULT (DATETIME()),
                last_seen TEXT NOT NULL DEFAULT (DATETIME()),
                rejected TEXT
            );
            CREATE TABLE state (
                key TEXT NOT NULL UNIQUE,
                value TEXT
            );
        ''')
    return conn

@app.teardown_appcontext
def close_conn(exn):
    if hasattr(flask.g, '_conn'):
        flask.g._conn.close()

@app.route('/refresh')
def refresh():
    pets = requests.get('http://api.petfinder.com/pet.find', params=CONFIG['params']).json()
    pets = demangle(pets)['petfinder']['pets']
    def pet_to_params(pet):
        return int(pet['id']), json.dumps(pet)
    pets = map(pet_to_params, pets)

    conn = get_conn()
    conn.execute('''
        CREATE TABLE temp.pet (
            pet_id INTEGER PRIMARY KEY,
            blob TEXT NOT NULL
        );
    ''')
    conn.executemany('INSERT INTO temp.pet (pet_id, blob) VALUES (?, ?)', pets)
    conn.executescript('''
        INSERT OR IGNORE INTO main.pet (pet_id, blob)
        SELECT pet_id, blob FROM temp.pet;

        UPDATE main.pet SET last_seen = DATETIME()
        WHERE EXISTS (SELECT * FROM temp.pet WHERE temp.pet.pet_id = main.pet.pet_id);

        INSERT OR REPLACE INTO state (key, value)
        VALUES ('last_refresh', DATETIME());

        DROP TABLE temp.pet;
    ''')
    # DROP TABLE is implicit commit

    return flask.redirect(flask.url_for('index'))

@app.route('/reject/<int:id>')
def reject(id):
    conn = get_conn()
    conn.execute('UPDATE pet SET rejected = DATETIME() WHERE pet_id = ?', (id,))
    conn.commit()
    return flask.redirect(flask.url_for('index'))

@app.route('/')
def index():
    pets = []
    cursor = get_conn().execute('''
        SELECT blob FROM pet
        WHERE last_seen >= (SELECT value FROM state WHERE key = 'last_refresh')
        AND rejected IS NULL
        AND json_extract(blob, '$.shelterId') LIKE ? || '%'
    ''', (CONFIG['state_abbrev'],))
    for row in cursor:
        pet = json.loads(row[0])

        # filter out everything but large photos
        def large_photo(p):
            return '&width=500&' in p
        pet['media']['photos'] = filter(large_photo, pet['media']['photos'])

        # truncate description
        if len(pet['description']) > 120:
            pet['description'] = pet['description'][:120] + '...'

        pets.append(pet)
    return flask.render_template('index.html', pets=pets)

if __name__ == '__main__':
    app.run(debug=True)
