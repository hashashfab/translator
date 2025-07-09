"""
app.py – Flask-SocketIO rule-based translator + collaborative C++ workbench
with real-time multiple-cursor support via Monaco Editor.
Run:  python app.py  (requires Flask==2.x, python-socketio, flask-socketio)
"""

import re, random, time
from flask import Flask, render_template, request, Response
from flask_socketio import SocketIO, emit

# ───────────────────────────────────────────────────────────────────────────────
# 1.  SHARED FLASK + SOCKET.IO INITIALISATION
# ───────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'my-super-secret-merged-app-key!'
socketio = SocketIO(app, cors_allowed_origins="*")   # allow local dev from file://

# ───────────────────────────────────────────────────────────────────────────────
# 2.  RULE-BASED EN→RU TRANSLATOR
# ───────────────────────────────────────────────────────────────────────────────
class RuleBasedTranslator:
    """A tiny deterministic English → simplified Russian translator."""
    def __init__(self):
        self.lexicon = {
            'boy':   {'pos': 'noun', 'tr': 'мальчик',  'g': 'm', 'n': 's'},
            'boys':  {'pos': 'noun', 'tr': 'мальчики', 'g': 'm', 'n': 'p'},
            'girl':  {'pos': 'noun', 'tr': 'девочка',  'g': 'f', 'n': 's'},
            'girls': {'pos': 'noun', 'tr': 'девочки',  'g': 'f', 'n': 'p'},
            'cat':   {'pos': 'noun', 'tr': 'кот',      'g': 'm', 'n': 's'},
            'cats':  {'pos': 'noun', 'tr': 'коты',     'g': 'm', 'n': 'p'},
            'book':  {'pos': 'noun', 'tr': 'книга',    'g': 'f', 'n': 's'},
            'books': {'pos': 'noun', 'tr': 'книги',    'g': 'f', 'n': 'p'},
            'apple': {'pos': 'noun', 'tr': 'яблоко',   'g': 'n', 'n': 's'},
            'apples':{'pos': 'noun', 'tr': 'яблоки',   'g': 'n', 'n': 'p'},

            'reads': {'pos': 'verb', 'tr': 'читает'}, 'read': {'pos': 'verb', 'tr': 'читают'},
            'eats':  {'pos': 'verb', 'tr': 'ест'},    'eat':  {'pos': 'verb', 'tr': 'едят'},
            'sees':  {'pos': 'verb', 'tr': 'видит'},  'see':  {'pos': 'verb', 'tr': 'видят'},

            'big':   {'pos': 'adj', 'tr': {'m':'большой','f':'большая','n':'большое','p':'большие'}},
            'small': {'pos': 'adj', 'tr': {'m':'маленький','f':'маленькая','n':'маленькое','p':'маленькие'}},
            'red':   {'pos': 'adj', 'tr': {'m':'красный','f':'красная','n':'красное','p':'красные'}},
            'green': {'pos': 'adj', 'tr': {'m':'зелёный','f':'зелёная','n':'зелёное','p':'зелёные'}},
            'the': {'pos':'art'}, 'a': {'pos':'art'}, 'an': {'pos':'art'},
        }

    def translate(self, text: str) -> str:
        words = re.findall(r'\b\w+\b', text.lower())
        tokens = [self._analyse(w) for w in words if w not in ('the','a','an')]
        rus = [self._to_russian(tok, nxt) for tok, nxt in zip(tokens, tokens[1:]+[None])]
        return (' '.join(rus)).capitalize() + '.' if rus else ''

    def _analyse(self, word):
        if word=='an': word='a'
        return self.lexicon.get(word, {'pos':'unk', 'tr':f'[{word}]'})

    def _to_russian(self, tok, nxt):
        if tok['pos']=='adj' and nxt and nxt['pos']=='noun':
            g,n = nxt['g'], nxt['n']
            return tok['tr']['p' if n=='p' else g]
        return tok['tr']

translator = RuleBasedTranslator()

# ───────────────────────────────────────────────────────────────────────────────
# 3.  SHARED WORKBENCH STATE  (code, chat, cursors)
# ───────────────────────────────────────────────────────────────────────────────
INITIAL_CODE = """\
#include <iostream>

int main() {
    // Welcome to the Collaborative C++ Code Workbench!
    // Multiple users can edit this code in real-time.
    std::cout << "Hello, collaborative world!" << std::endl;
    return 0;
}
"""

class WorkbenchState:
    USER_COLORS = ['#FF6B6B','#4ECDC4','#45B7D1','#FED766','#F0B37E','#77DD77','#CDB4DB']
    def __init__(self):
        self.code = INITIAL_CODE
        self.chat = []                 # latest 100 lines
        self.users = {}                # sid → dict(username,color)
        self.cursors = {}              # sid → cursor+selection info

    # user management ----------------------------------------------------------
    def add_user(self, sid, name):
        self.users[sid] = {'username': name, 'color': random.choice(self.USER_COLORS)}
        self.cursors[sid] = None
    def remove_user(self, sid):
        self.users.pop(sid, None)
        self.cursors.pop(sid, None)

    # chat ---------------------------------------------------------------------
    def add_chat(self, entry):
        self.chat.append(entry)
        if len(self.chat) > 100:
            self.chat.pop(0)

    # cursor -------------------------------------------------------------------
    def set_cursor(self, sid, data):
        if sid in self.cursors:
            self.cursors[sid] = data
    def visible_cursors(self, exclude_sid=None):
        return {sid:{'cursor':c,'user':self.users[sid]}
                for sid,c in self.cursors.items()
                if sid!=exclude_sid and c and sid in self.users}

wb = WorkbenchState()

# ───────────────────────────────────────────────────────────────────────────────
# 5.  ROUTES
# ───────────────────────────────────────────────────────────────────────────────

@app.route('/translator')
def translator_page():
    return render_template('translator.html')

@app.route('/workbench')
def workbench_page():
    return render_template('workbench.html')

@app.route('/favicon.ico')          # silence 404s in dev
def favicon(): return Response(status=204)

# ───────────────────────────────────────────────────────────────────────────────
# 6.  SOCKET.IO EVENT HANDLERS
# ───────────────────────────────────────────────────────────────────────────────
# Translator -------------------------------------------------
@socketio.on('translate_request')
def on_translate(msg): emit('translation_result',{'translation':translator.translate(msg.get('text',''))})

@socketio.on('request_workbench')
def on_reqwb(): emit('redirect',{'url':'/workbench'})

# Workbench – users -----------------------------------------
@socketio.on('set_username')
def on_set_username(name):
    wb.add_user(request.sid, name)
    emit('init_state',{'code':wb.code,'cursors':wb.visible_cursors(exclude_sid=request.sid)})

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in wb.users:
        emit('cursor_remove',{'userId':request.sid},broadcast=True,include_self=False)
    wb.remove_user(request.sid)

# Workbench – code ------------------------------------------
@socketio.on('code_update')
def on_code_update(code):
    if request.sid in wb.users:
        wb.code = code
        emit('code_update',code,broadcast=True,include_self=False)

# Workbench – cursors ---------------------------------------
@socketio.on('cursor_update')
def on_cursor(data):
    if request.sid in wb.users:
        wb.set_cursor(request.sid,{'cursor':data})
        emit('cursor_update',{
            'userId':request.sid,'cursor':data,
            'user':wb.users[request.sid]},broadcast=True,include_self=False)

# Workbench – chat ------------------------------------------
@socketio.on('chat_message')
def on_chat(msg):
    user=wb.users.get(request.sid)
    if user:
        entry={'username':user['username'],'message':msg}
        wb.add_chat(entry)
        socketio.emit('chat_message',entry)

# ───────────────────────────────────────────────────────────────────────────────
# 7.  MAIN
# ───────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    socketio.run(app,host='0.0.0.0',port=5000,debug=True)


