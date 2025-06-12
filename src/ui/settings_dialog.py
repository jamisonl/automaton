import tkinter as tk
from tkinter import ttk, messagebox
import threading
from typing import List, Callable
from core.config import (
    fetch_available_models,
    get_model_name,
    set_model_name,
    get_default_models,
)


class SettingsDialog:
    def __init__(self, parent, on_settings_changed: Callable = None):
        self.parent = parent
        self.on_settings_changed = on_settings_changed
        self.dialog = None
        self.current_model = tk.StringVar()
        self.available_models = []
        self.model_dropdown = None
        self.refresh_button = None
        self.status_label = None

    def show(self):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return

        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Settings")
        self.dialog.geometry("500x400")
        self.dialog.resizable(True, True)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        self.current_model.set(get_model_name())

        self._create_widgets()
        self._load_models()

        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

    def _create_widgets(self):
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill="both", expand=True)

        title_label = ttk.Label(
            main_frame, text="Application Settings", font=("Arial", 14, "bold")
        )
        title_label.pack(anchor="w", pady=(0, 20))

        model_frame = ttk.LabelFrame(
            main_frame, text="LLM Model Configuration", padding="15"
        )
        model_frame.pack(fill="x", pady=(0, 20))

        model_label = ttk.Label(model_frame, text="Gemini Model:")
        model_label.pack(anchor="w", pady=(0, 5))

        model_selection_frame = ttk.Frame(model_frame)
        model_selection_frame.pack(fill="x", pady=(0, 10))

        self.model_dropdown = ttk.Combobox(
            model_selection_frame,
            textvariable=self.current_model,
            state="readonly",
            width=50,
        )
        self.model_dropdown.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.refresh_button = ttk.Button(
            model_selection_frame,
            text="🔄 Refresh",
            command=self._refresh_models,
            width=10,
        )
        self.refresh_button.pack(side="right")

        self.status_label = ttk.Label(
            model_frame, text="Loading models...", font=("Arial", 9), foreground="gray"
        )
        self.status_label.pack(anchor="w", pady=(5, 0))

        info_frame = ttk.Frame(model_frame)
        info_frame.pack(fill="x", pady=(10, 0))

        info_text = tk.Text(
            info_frame,
            height=8,
            wrap=tk.WORD,
            font=("Arial", 9),
            bg="#f8f9fa",
            relief="flat",
            padx=10,
            pady=10,
        )
        info_text.pack(fill="x")

        model_info = """Available Gemini Models:

• gemini-2.5-flash-preview-05-20: Adaptive thinking, cost efficiency
• gemini-2.5-pro-preview-06-05: Enhanced reasoning, advanced coding

Choose the model that best fits your needs. The flash model is faster and more cost-effective, while the pro model offers enhanced reasoning capabilities."""

        info_text.insert("1.0", model_info)
        info_text.config(state="disabled")

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(20, 0))

        cancel_button = ttk.Button(button_frame, text="Cancel", command=self._on_cancel)
        cancel_button.pack(side="right", padx=(10, 0))

        save_button = ttk.Button(
            button_frame, text="Save Settings", command=self._on_save
        )
        save_button.pack(side="right")

        self.dialog.bind("<Return>", lambda e: self._on_save())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

    def _load_models(self):
        self.status_label.config(text="Loading models from API...", foreground="blue")
        self.refresh_button.config(state="disabled")

        def load_models_task():
            try:
                models = fetch_available_models()
                self.dialog.after(0, self._on_models_loaded, models, None)
            except Exception as e:
                self.dialog.after(0, self._on_models_loaded, None, str(e))

        threading.Thread(target=load_models_task, daemon=True).start()

    def _refresh_models(self):
        self._load_models()

    def _on_models_loaded(self, models: List[str], error: str):
        self.refresh_button.config(state="normal")

        if error:
            self.status_label.config(
                text=f"Failed to load models: {error[:50]}...", foreground="red"
            )
            models = get_default_models()
            self.status_label.config(
                text="Using default model list (API unavailable)", foreground="orange"
            )
        else:
            self.status_label.config(
                text=f"Loaded {len(models)} available models", foreground="green"
            )

        self.available_models = models
        self.model_dropdown["values"] = models

        current = self.current_model.get()
        if current not in models and models:
            self.current_model.set(models[0])

    def _on_save(self):
        selected_model = self.current_model.get()

        if not selected_model:
            messagebox.showerror("Error", "Please select a model")
            return

        if selected_model not in self.available_models:
            messagebox.showerror("Error", "Selected model is not available")
            return

        try:
            set_model_name(selected_model)

            if self.on_settings_changed:
                self.on_settings_changed(selected_model)

            messagebox.showinfo("Success", "Settings saved successfully!")
            self._close_dialog()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {str(e)}")

    def _on_cancel(self):
        self._close_dialog()

    def _close_dialog(self):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.grab_release()
            self.dialog.destroy()
            self.dialog = None
