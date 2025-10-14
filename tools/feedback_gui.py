import json
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


BASE_DIR = Path(__file__).resolve().parent.parent
FEEDBACK_DIR = BASE_DIR / "folders" / "feedback"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug or "documento"


def _register_feedback(documento: str, status: str, nova_categoria: str, observacoes: str, autor: str) -> Path:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "documento": documento.strip(),
        "status": status.strip().lower() or "correto",
        "nova_categoria": nova_categoria.strip(),
        "observacoes": observacoes.strip(),
        "autor": autor.strip(),
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    slug = _slugify(payload["documento"])
    filename = f"feedback_{slug}_{int(time.time())}.json"
    target = FEEDBACK_DIR / filename
    with open(target, "w", encoding="utf-8") as handler:
        json.dump(payload, handler, indent=2, ensure_ascii=False)
        handler.write("\n")
    return target


def _submit(form):
    documento = form["documento"].get().strip()
    if not documento:
        messagebox.showwarning("Feedback", "Informe o nome do documento analisado.")
        return
    target = _register_feedback(
        documento=documento,
        status=form["status"].get(),
        nova_categoria=form["nova_categoria"].get(),
        observacoes=form["observacoes"].get("1.0", tk.END),
        autor=form["autor"].get(),
    )
    messagebox.showinfo(
        "Feedback registrado",
        f"Feedback salvo em:\n{target}",
    )
    form["observacoes"].delete("1.0", tk.END)


def main() -> None:
    root = tk.Tk()
    root.title("Feedback Classifica")
    root.resizable(False, False)

    main_frame = ttk.Frame(root, padding=12)
    main_frame.grid(row=0, column=0, sticky="nsew")

    form = {}

    ttk.Label(main_frame, text="Documento analisado *").grid(row=0, column=0, sticky="w")
    form["documento"] = ttk.Entry(main_frame, width=50)
    form["documento"].grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 8))

    ttk.Label(main_frame, text="Status da classificacao").grid(row=2, column=0, sticky="w")
    form["status"] = ttk.Combobox(main_frame, values=["correto", "incorreto"], state="readonly", width=20)
    form["status"].set("correto")
    form["status"].grid(row=3, column=0, sticky="w", pady=(0, 8))

    ttk.Label(main_frame, text="Categoria correta (se aplicavel)").grid(row=4, column=0, sticky="w")
    form["nova_categoria"] = ttk.Entry(main_frame, width=40)
    form["nova_categoria"].grid(row=5, column=0, columnspan=2, sticky="we", pady=(0, 8))

    ttk.Label(main_frame, text="Observacoes").grid(row=6, column=0, sticky="w")
    form["observacoes"] = tk.Text(main_frame, width=60, height=8)
    form["observacoes"].grid(row=7, column=0, columnspan=2, sticky="we", pady=(0, 8))

    ttk.Label(main_frame, text="Autor (opcional)").grid(row=8, column=0, sticky="w")
    form["autor"] = ttk.Entry(main_frame, width=40)
    form["autor"].grid(row=9, column=0, columnspan=2, sticky="we", pady=(0, 8))

    action_frame = ttk.Frame(main_frame)
    action_frame.grid(row=10, column=0, columnspan=2, sticky="e")

    submit_btn = ttk.Button(action_frame, text="Enviar feedback", command=lambda: _submit(form))
    submit_btn.grid(row=0, column=0, padx=4)

    close_btn = ttk.Button(action_frame, text="Fechar", command=root.destroy)
    close_btn.grid(row=0, column=1, padx=4)

    for child in main_frame.winfo_children():
        child.grid_configure(padx=4, pady=2)

    root.mainloop()


if __name__ == "__main__":
    main()
