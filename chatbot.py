import os
import re
import sqlite3
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, font as tkfont
from openai import OpenAI
#troque essa chave da api open ai
OPENAI_API_KEY = ""
client = OpenAI(api_key=OPENAI_API_KEY)
DB_PATH = "saude.db"

CREATE_TABLES_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS hospitais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cidade TEXT NOT NULL,
    contato TEXT,
    avaliacao REAL
);
CREATE TABLE IF NOT EXISTS medicos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    especialidade TEXT NOT NULL,
    hospital_id INTEGER,
    descricao TEXT,
    FOREIGN KEY (hospital_id) REFERENCES hospitais (id)
);
CREATE TABLE IF NOT EXISTS sintomas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE,
    possiveis_causas TEXT,
    especialistas TEXT
);
"""

SEED_HOSPITAIS = [
    ("Hospital Santa Casa de Bom Despacho", "Bom Despacho", "(37) 3521-1234", 4.5),
    ("Clínica Vida Mais", "Bom Despacho", "(37) 3522-9876", 4.3),
    ("Centro Médico Bom Despacho", "Bom Despacho", "(37) 3521-4567", 4.4),
    ("Hospital São João de Deus", "Divinópolis", "(37) 3229-7500", 4.7),
]

SEED_MEDICOS = [
    ("Dr. João Mendes", "clínico geral", "Hospital Santa Casa de Bom Despacho", "avalia sintomas diversos e orienta primeiros cuidados"),
    ("Dra. Ana Carvalho", "cardiologista", "Centro Médico Bom Despacho", "especialista em dores no peito e hipertensão"),
    ("Dr. Rafael Nogueira", "neurologista", "Clínica Vida Mais", "atua com enxaqueca, tontura e sono"),
    ("Dra. Luiza Tavares", "gastroenterologista", "Hospital Santa Casa de Bom Despacho", "trata dor abdominal, refluxo e gastrite"),
    ("Dr. Pedro Lima", "pneumologista", "Hospital São João de Deus", "atende asma, tosse e falta de ar"),
]

SEED_SINTOMAS = [
    ("dor de cabeça", "enxaqueca,tensão muscular,problemas de visão,sinusite", "neurologista,clínico geral"),
    ("dor no peito", "angina,ansiedade,problemas musculares,refluxo", "cardiologista,clínico geral"),
    ("falta de ar", "asma,ansiedade,doença pulmonar,problemas cardíacos", "pneumologista,cardiologista"),
    ("febre", "infecção viral,infecção bacteriana,viroses", "infectologista,clínico geral"),
    ("tosse", "resfriado,bronquite,asma,alergia", "pneumologista,clínico geral"),
    ("dor abdominal", "gastrite,úlcera,infecção intestinal,apendicite", "gastroenterologista,clínico geral"),
]

def init_db():
    created = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(CREATE_TABLES_SQL)
    conn.commit()
    if created:
        cur.executemany("INSERT INTO hospitais (nome, cidade, contato, avaliacao) VALUES (?, ?, ?, ?)", SEED_HOSPITAIS)
        conn.commit()
        cur.execute("SELECT id, nome FROM hospitais")
        hospital_map = {n: i for i, n in cur.fetchall()}
        med_rows = [(n, e, hospital_map.get(h), d) for n, e, h, d in SEED_MEDICOS]
        cur.executemany("INSERT INTO medicos (nome, especialidade, hospital_id, descricao) VALUES (?, ?, ?, ?)", med_rows)
        cur.executemany("INSERT INTO sintomas (nome, possiveis_causas, especialistas) VALUES (?, ?, ?)", SEED_SINTOMAS)
        conn.commit()
    conn.close()

def buscar_hospitais_por_cidade_e_especialidade(cidade, especialidade, limit=3):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT h.nome, h.contato, h.avaliacao, m.nome, m.descricao
        FROM hospitais h
        JOIN medicos m ON h.id = m.hospital_id
        WHERE lower(h.cidade)=lower(?) AND lower(m.especialidade)=lower(?)
        ORDER BY h.avaliacao DESC LIMIT ?
    """, (cidade, especialidade, limit))
    res = cur.fetchall()
    conn.close()
    return res

def buscar_sintoma(nome):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT possiveis_causas, especialistas FROM sintomas WHERE lower(nome)=lower(?)", (nome,))
    row = cur.fetchone()
    conn.close()
    return row

def chamar_chatgpt(prompt_user, contexto_local=None):
    try:
        base_system_prompt = (
            "Você é um assistente virtual de saúde. Use informações locais sobre hospitais, médicos e sintomas para enriquecer a resposta. "
            "Seja empático, direto e profissional. Evite diagnósticos e recomende procurar um médico quando necessário."
        )
        if contexto_local:
            base_system_prompt += f"\n\nBase local:\n{contexto_local.strip()}"
        messages = [
            {"role": "system", "content": base_system_prompt},
            {"role": "user", "content": prompt_user}
        ]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.28,
            max_tokens=450
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f" Erro ao conectar ao ChatGPT: {e}"

class ChatbotTriagem:
    def __init__(self, nome="SUSI"):
        self.nome = nome
        init_db()
    def _extrair_sintomas(self, texto):
        texto = texto.lower()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT nome FROM sintomas")
        sintomas = [r[0] for r in cur.fetchall()]
        conn.close()
        return [s for s in sintomas if re.search(rf"\b{re.escape(s)}\b", texto)]
    def _montar_resposta_local(self, sintomas, cidade=None):
        partes = []
        for s in sintomas:
            dados = buscar_sintoma(s)
            if not dados: continue
            causas, especialistas = dados
            causas, especialistas = causas.split(","), especialistas.split(",")
            parte = f"{s.title()} pode estar relacionado a: {', '.join(causas)}.\nEspecialistas: {', '.join(especialistas)}.\n"
            if cidade:
                for esp in especialistas:
                    hosps = buscar_hospitais_por_cidade_e_especialidade(cidade, esp.strip())
                    if hosps:
                        parte += f"\nEm {cidade.title()}, opções de {esp.strip()}:\n"
                        for hnome, hcontato, haval, mnome, mdesc in hosps:
                            parte += f"- {mnome} — {hnome} ({hcontato}) — Nota {haval}. {mdesc}\n"
            partes.append(parte)
        return "\n".join(partes)
    def responder(self, texto):
        if not texto.strip():
            return "Por favor, descreva seus sintomas ou dúvidas."
        if any(p in texto.lower() for p in ["dor forte no peito", "falta de ar intensa", "desmaio", "sangramento"]):
            return "🚨 Isso pode ser uma emergência. Procure atendimento médico IMEDIATAMENTE."
        cidade = None
        m = re.search(r"em ([a-zçãâêôéíóú ]+)", texto, re.IGNORECASE)
        if m: cidade = m.group(1).strip()
        sintomas = self._extrair_sintomas(texto)
        if sintomas:
            contexto_local = self._montar_resposta_local(sintomas, cidade)
            prompt = f"O usuário relatou: '{texto}'. Com base nas informações locais, oriente de forma empática, sem diagnósticos.\n\n{contexto_local}"
            return chamar_chatgpt(prompt)
        else:
            prompt = f"O usuário disse: '{texto}'. Dê uma resposta breve e empática sobre saúde, sem diagnóstico."
            return chamar_chatgpt(prompt)

class MessengerUI:
    def __init__(self):
        self.bot = ChatbotTriagem()
        self.root = tk.Tk()
        self.root.title("SUSI — Triagem")
        self.root.geometry("820x660")
        self.root.configure(bg="#F2F3F5")
        self.root.resizable(False, False)
        self.font_small = tkfont.Font(family="Helvetica", size=9)
        self.font_normal = tkfont.Font(family="Helvetica", size=11)
        self.font_bold = tkfont.Font(family="Helvetica", size=11, weight="bold")
        self.user_bubble = "#0084FF"
        self.bot_bubble = "#E9EAEB"
        self.user_text_color = "#FFFFFF"
        self.bot_text_color = "#111827"
        self.bg = "#F2F3F5"
        self._build_header()
        self._build_message_area()
        self._build_input_area()
        self.vpad = 12
        self.xpad = 16
        self.line_y = 14
        self._add_bot_message("Olá! Sou o SUSI. Descreva seus sintomas ou dúvidas sobre saúde. Posso sugerir especialistas e hospitais locais.")

    def _build_header(self):
        header = tk.Frame(self.root, bg=self.bg, height=80)
        header.pack(fill=tk.X)
        avatar = tk.Canvas(header, width=52, height=52, bg=self.bg, highlightthickness=0)
        avatar.create_oval(6, 6, 46, 46, fill="#3b82f6", outline="")
        avatar.create_text(26, 28, text="DA", fill="white", font=self.font_bold)
        avatar.pack(side=tk.LEFT, padx=18, pady=14)
        title_frame = tk.Frame(header, bg=self.bg)
        title_frame.pack(side=tk.LEFT, anchor="w")
        tk.Label(title_frame, text="SUSI", font=self.font_bold, bg=self.bg, fg="#0f172a").pack(anchor="w")
        tk.Label(title_frame, text="Triagem de Saúde • Atendimento automatizado", font=self.font_small, bg=self.bg, fg="#475569").pack(anchor="w")

    def _build_message_area(self):
        container = tk.Frame(self.root, bg=self.bg)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.canvas = tk.Canvas(container, bg=self.bg, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.messages_frame = tk.Frame(self.canvas, bg=self.bg)
        self.canvas.create_window((0, 0), window=self.messages_frame, anchor="nw")
        self.messages_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.msg_max_width = 520
        self._add_spacer(6)

    def _build_input_area(self):
        input_frame = tk.Frame(self.root, bg="#ffffff", height=78)
        input_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=12)
        self.user_entry = tk.Entry(input_frame, font=self.font_normal, bd=0, relief=tk.FLAT)
        self.user_entry.pack(side=tk.LEFT, padx=(14, 8), pady=12, ipadx=6, ipady=8, fill=tk.X, expand=True)
        self.user_entry.bind("<Return>", self._on_send)
        send_btn = tk.Button(input_frame, text="Enviar", command=self._on_send_button,
                             bg=self.user_bubble, fg="white", bd=0, font=self.font_bold,
                             activebackground="#006fe8", padx=12, pady=8)
        send_btn.pack(side=tk.RIGHT, padx=10, pady=10)
        clear_btn = tk.Button(input_frame, text="Limpar", command=self._clear_chat,
                              bg="#F3F4F6", fg="#0f172a", bd=0, font=self.font_small, padx=8)
        clear_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=10)

    def _add_spacer(self, h):
        spacer = tk.Frame(self.messages_frame, height=h, bg=self.bg)
        spacer.pack(fill=tk.X)

    def _timestamp(self):
        return datetime.now().strftime("%H:%M")

    def _create_bubble_widget(self, text, is_user=False):
        wraplength = self.msg_max_width
        frame = tk.Frame(self.messages_frame, bg=self.bg)
        avatar_canvas = tk.Canvas(frame, width=36, height=36, bg=self.bg, highlightthickness=0)
        if is_user:
            avatar_canvas.create_oval(2, 2, 34, 34, fill="#2563EB", outline="")
            avatar_canvas.create_text(18, 18, text="VC", fill="white", font=self.font_small)
        else:
            avatar_canvas.create_oval(2, 2, 34, 34, fill="#64748B", outline="")
            avatar_canvas.create_text(18, 18, text="DA", fill="white", font=self.font_small)
        bubble_color = self.user_bubble if is_user else self.bot_bubble
        text_color = self.user_text_color if is_user else self.bot_text_color
        bubble = tk.Label(frame, text=text, bg=bubble_color, fg=text_color,
                          wraplength=wraplength, justify="left" if not is_user else "left",
                          font=self.font_normal, bd=0, padx=12, pady=8)
        ts = tk.Label(frame, text=self._timestamp(), bg=self.bg, fg="#94a3b8", font=self.font_small)
        if is_user:
            bubble.pack(side=tk.RIGHT, anchor="e", padx=(6, 2))
            avatar_canvas.pack(side=tk.RIGHT, anchor="e", padx=(6, 8))
            ts.pack(side=tk.RIGHT, anchor="e", padx=(0, 6), pady=(4, 0))
        else:
            avatar_canvas.pack(side=tk.LEFT, anchor="w", padx=(8, 6))
            bubble.pack(side=tk.LEFT, anchor="w", padx=(2, 6))
            ts.pack(side=tk.LEFT, anchor="w", padx=(6, 0), pady=(4, 0))
        return frame

    def _add_user_message(self, text):
        frame = self._create_bubble_widget(text, is_user=True)
        frame.pack(fill=tk.X, padx=12, pady=(6, 2), anchor="e")
        self.root.after(50, lambda: self.canvas.yview_moveto(1.0))

    def _add_bot_message(self, text):
        frame = self._create_bubble_widget(text, is_user=False)
        frame.pack(fill=tk.X, padx=12, pady=(6, 2), anchor="w")
        self.root.after(50, lambda: self.canvas.yview_moveto(1.0))

    def _typing_indicator(self):
        anim_frame = tk.Frame(self.messages_frame, bg=self.bg)
        avatar_canvas = tk.Canvas(anim_frame, width=36, height=36, bg=self.bg, highlightthickness=0)
        avatar_canvas.create_oval(2, 2, 34, 34, fill="#64748B", outline="")
        avatar_canvas.create_text(18, 18, text="DA", fill="white", font=self.font_small)
        avatar_canvas.pack(side=tk.LEFT, anchor="w", padx=(8, 6))
        dot_label = tk.Label(anim_frame, text="• • •", font=self.font_bold, bg=self.bot_bubble, fg=self.bot_text_color, padx=12, pady=8)
        dot_label.pack(side=tk.LEFT, anchor="w", padx=(2, 6))
        anim_frame.pack(fill=tk.X, padx=12, pady=(6, 2), anchor="w")
        self.canvas.yview_moveto(1.0)
        return anim_frame, dot_label

    def _on_send_button(self):
        self._on_send(None)

    def _on_send(self, event):
        text = self.user_entry.get().strip()
        if not text:
            return
        self.user_entry.delete(0, tk.END)
        self._add_user_message(text)
        threading.Thread(target=self._process_user_input, args=(text,), daemon=True).start()

    def _process_user_input(self, text):
        anim_frame, dot_label = self._typing_indicator()
        stop_anim = threading.Event()
        def animate_dots():
            states = ["•  ", "•• ", "•••", " ••", "  •"]
            idx = 0
            while not stop_anim.is_set():
                dot_label.config(text=states[idx % len(states)])
                idx += 1
                time.sleep(0.45)
        t = threading.Thread(target=animate_dots, daemon=True)
        t.start()
        try:
            resposta = self.bot.responder(text)
        except Exception as e:
            resposta = f"Erro interno: {e}"
        stop_anim.set()
        t.join(timeout=0.1)
        self.root.after(0, lambda: anim_frame.destroy())
        self.root.after(30, lambda: self._add_bot_message(resposta))

    def _clear_chat(self):
        for widget in self.messages_frame.winfo_children():
            widget.destroy()
        self._add_spacer(6)
        self._add_bot_message("Chat reiniciado. Descreva seus sintomas ou dúvidas sobre saúde.")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    MessengerUI().run()


