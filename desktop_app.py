#!/usr/bin/env python3
"""Desktop application wrapper for Automaton."""

import sys
import io


early_captured_stderr = sys.stderr
stable_log_stream = None

if hasattr(early_captured_stderr, "buffer") and not isinstance(early_captured_stderr, io.TextIOWrapper):
    try:
        stable_log_stream = io.TextIOWrapper(
            early_captured_stderr.buffer,
            encoding="utf-8", errors="replace", newline="\n", line_buffering=True
        )
    except Exception:
        stable_log_stream = early_captured_stderr 
else:
    stable_log_stream = early_captured_stderr
from src.core.logger import setup_logger, logger as global_logger_ref

configured_logger = setup_logger(output_stream=stable_log_stream)
global_logger_ref = configured_logger 

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv not found, .env file not loaded.", file=sys.stderr)
    sys.stderr.flush()
    pass

import threading
import webbrowser
import time
import os
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.events import EventType
from io_layer.progress_publisher import ProgressEventType
from io_layer.native_file_browser import NativeFileBrowser
from ui.settings_dialog import SettingsDialog
import tkinter as tk
from tkinter import ttk, messagebox

class LLMAgentDesktopApp:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("🤖 Automaton")
        self.root.geometry("1200x800")
        self.root.resizable(True, True)

        self.current_repo_path = tk.StringVar()
        self.feature_specification = tk.StringVar()
        self.controller = None

        self.github_token = tk.StringVar()
        self.github_username = tk.StringVar()
        self.gemini_api_key = tk.StringVar()

        self.github_creds_status_text = tk.StringVar(value="(Not Validated)")

        self.setup_ui()
        global_logger_ref.info("UI setup complete.")
        self.load_credentials()
        global_logger_ref.info("Credentials loading complete.")
        self.setup_controller()
        global_logger_ref.info("Controller setup process finished.")

        self._trigger_initial_gemini_validation()
        self._trigger_initial_github_validation()
        global_logger_ref.info("LLMAgentDesktopApp.__init__ finished.")

    def setup_ui(self):
        global_logger_ref.debug("setup_ui called")
        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill="x", padx=20, pady=20)

        title_label = ttk.Label(
            title_frame, text="🤖 Automaton", font=("Arial", 16, "bold")
        )
        title_label.pack(side="left")

        settings_btn = ttk.Button(
            title_frame, text="⚙️ Settings", command=self.show_settings
        )
        settings_btn.pack(side="right")

        creds_main_frame = ttk.Frame(self.root)
        creds_main_frame.pack(fill="x", padx=20, pady=10)

        creds_header = ttk.Frame(creds_main_frame)
        creds_header.pack(fill="x")

        self.creds_expanded = tk.BooleanVar(value=False)
        self.toggle_creds_btn = ttk.Button(
            creds_header,
            text="🔑 Configure Credentials ▶",
            command=self.toggle_credentials,
        )
        self.toggle_creds_btn.pack(side="left")

        self.creds_status_label = ttk.Label(creds_header, text="")
        self.creds_status_label.pack(side="right")

        self.creds_frame = ttk.LabelFrame(
            creds_main_frame, text="🔑 Credentials", padding="10"
        )

        creds_grid = ttk.Frame(self.creds_frame)
        creds_grid.pack(fill="x")

        ttk.Label(creds_grid, text="GitHub Token:").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        github_token_entry = ttk.Entry(
            creds_grid, textvariable=self.github_token, show="*", width=40
        )
        github_token_entry.grid(row=0, column=1, sticky="ew", padx=(0, 5))

        ttk.Label(creds_grid, text="GitHub Username:").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=(5, 0)
        )
        github_user_entry = ttk.Entry(
            creds_grid, textvariable=self.github_username, width=40
        )
        github_user_entry.grid(row=1, column=1, sticky="ew", padx=(0, 5), pady=(5, 0))

        self.github_creds_status_label = ttk.Label(
            creds_grid, textvariable=self.github_creds_status_text, font=("Arial", 8)
        )
        self.github_creds_status_label.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="w",
            padx=(5, 0),
            pady=(0, 0),
        )

        ttk.Label(creds_grid, text="Gemini API Key:").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=(15, 0)
        )
        gemini_key_entry = ttk.Entry(
            creds_grid,
            textvariable=self.gemini_api_key,
            show="*",
            width=40,
        )
        gemini_key_entry.grid(row=2, column=1, sticky="ew", padx=(0, 5), pady=(15, 0))

        self.gemini_key_status_label = ttk.Label(
            creds_grid, text="(Not Validated)", font=("Arial", 8)
        )
        self.gemini_key_status_label.grid(
            row=2, column=2, sticky="w", padx=(5, 0), pady=(15, 0)
        )

        creds_grid.columnconfigure(1, weight=1)
        creds_grid.columnconfigure(2, weight=0)

        save_creds_btn = ttk.Button(
            self.creds_frame, text="💾 Save Credentials", command=self.save_credentials
        )
        save_creds_btn.pack(pady=(10, 0))

        self.update_credentials_display()

        project_frame = ttk.LabelFrame(
            self.root, text="📁 Project Folder", padding="10"
        )
        project_frame.pack(fill="x", padx=20, pady=10)

        info_label = ttk.Label(
            project_frame,
            text="Select your project folder (git repo will be created automatically if needed):",
            font=("Arial", 9),
            foreground="gray",
        )
        info_label.pack(anchor="w", pady=(0, 5))

        ttk.Label(project_frame, text="Selected Project Folder:").pack(anchor="w")
        path_entry = ttk.Entry(
            project_frame, textvariable=self.current_repo_path, state="readonly"
        )
        path_entry.pack(fill="x", pady=(5, 10))

        browse_btn = ttk.Button(
            project_frame,
            text="📂 Browse for Project Folder",
            command=self.browse_repository,
        )
        browse_btn.pack(pady=5)

        feature_frame = ttk.LabelFrame(
            self.root, text="✨ Feature Request", padding="10"
        )
        feature_frame.pack(fill="x", padx=20, pady=10)

        ttk.Label(
            feature_frame, text="Describe the feature you want to implement:"
        ).pack(anchor="w")

        self.feature_text = tk.Text(feature_frame, height=4, wrap=tk.WORD)
        self.feature_text.pack(fill="x", pady=(5, 10))
        self.feature_text.insert(
            "1.0",
            "Make a flappybird game using html css and js",
        )

        self.submit_btn = ttk.Button(
            feature_frame, text="🚀 Submit Feature Request", command=self.submit_feature
        )
        self.submit_btn.pack(pady=5)

        status_frame = ttk.LabelFrame(
            self.root, text="📊 Status & Progress", padding="10"
        )
        status_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.status_text = tk.Text(
            status_frame, height=15, wrap=tk.WORD, state=tk.DISABLED
        )

        scrollbar = ttk.Scrollbar(
            status_frame, orient="vertical", command=self.status_text.yview
        )
        self.status_text.configure(yscrollcommand=scrollbar.set)

        self.status_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.add_status("🚀 Automaton ready!")
        self.add_status("📁 Please select a project folder to get started.")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _trigger_initial_gemini_validation(self):
        if (
            hasattr(self, "controller")
            and self.controller
            and self.gemini_api_key.get()
        ):
            self._validate_gemini_key_and_update_ui(self.gemini_api_key.get())
        elif hasattr(self, "gemini_key_status_label"):
            if not self.gemini_api_key.get():
                self.gemini_key_status_label.config(text="(Not Set)", foreground="gray")
            else:
                self.gemini_key_status_label.config(
                    text="(Not Validated)", foreground="gray"
                )

    def _validate_gemini_key_and_update_ui(self, api_key_to_validate: str):
        if not api_key_to_validate:
            if hasattr(self, "gemini_key_status_label"):
                self.root.after(
                    0,
                    lambda: self.gemini_key_status_label.config(
                        text="(Not Set)", foreground="gray"
                    ),
                )
            self.root.after(0, self.update_credentials_display)
            return

        if hasattr(self, "gemini_key_status_label"):
            self.root.after(
                0,
                lambda: self.gemini_key_status_label.config(
                    text="(Validating...)", foreground="blue"
                ),
            )

        def validation_task():
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if not self.controller:
                global_logger_ref.error("SystemController not available for Gemini validation.")
                if hasattr(self, "gemini_key_status_label"):
                    self.root.after(
                        0,
                        lambda: self.gemini_key_status_label.config(
                            text="(Error: No Controller)", foreground="red"
                        ),
                    )
                loop.close()
                return

            try:
                validation_result = loop.run_until_complete(
                    self.controller.validate_gemini_api_key(api_key_to_validate)
                )

                if hasattr(self, "gemini_key_status_label"):
                    if validation_result["valid"]:
                        self.root.after(
                            0,
                            lambda: self.gemini_key_status_label.config(
                                text="(Valid ✅)", foreground="green"
                            ),
                        )
                    else:
                        message = validation_result.get("message", "Invalid")
                        display_message = (
                            message[:30] + "..." if len(message) > 30 else message
                        )
                        self.root.after(
                            0,
                            lambda: self.gemini_key_status_label.config(
                                text=f"(Invalid ❌: {display_message})",
                                foreground="red",
                            ),
                        )
                        self.root.after(
                            0, self.add_status, f"🔑 Gemini API Key: {message}"
                        )
            except Exception as e:
                global_logger_ref.error(
                    f"Exception during Gemini key validation thread: {e}", exc_info=True
                )
                if hasattr(self, "gemini_key_status_label"):
                    self.root.after(
                        0,
                        lambda: self.gemini_key_status_label.config(
                            text="(Validation Error)", foreground="red"
                        ),
                    )
                self.root.after(
                    0, self.add_status, f"🔑 Gemini API Key validation error: {str(e)}"
                )
            finally:
                self.root.after(0, self.update_credentials_display)
                loop.close()
        threading.Thread(target=validation_task, daemon=True).start()

    def _trigger_initial_github_validation(self):
        if (
            hasattr(self, "controller")
            and self.controller
            and self.github_token.get()
            and self.github_username.get()
        ):
            self._validate_github_credentials_and_update_ui(
                self.github_token.get(), self.github_username.get()
            )
        elif hasattr(self, "github_creds_status_label"):
            if not self.github_token.get() or not self.github_username.get():
                self.github_creds_status_text.set("(Not Set)")
                if hasattr(self, "github_creds_status_label"):
                    self.github_creds_status_label.config(foreground="gray")
            else:
                self.github_creds_status_text.set("(Not Validated)")
                if hasattr(self, "github_creds_status_label"):
                    self.github_creds_status_label.config(foreground="gray")

    def _validate_github_credentials_and_update_ui(self, token: str, username: str):
        if not token or not username:
            self.github_creds_status_text.set("(Not Set)")
            if hasattr(self, "github_creds_status_label"):
                self.github_creds_status_label.config(foreground="gray")
            self.root.after(0, self.update_credentials_display)
            return

        self.github_creds_status_text.set("(Validating...)")
        if hasattr(self, "github_creds_status_label"):
            self.github_creds_status_label.config(foreground="blue")

        def validation_task():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if not self.controller:
                global_logger_ref.error("SystemController not available for GitHub validation.")
                self.github_creds_status_text.set("(Error: No Controller)")
                if hasattr(self, "github_creds_status_label"):
                    self.github_creds_status_label.config(foreground="red")
                loop.close()
                return

            try:
                validation_result = loop.run_until_complete(
                    self.controller.validate_github_credentials(token, username)
                )
                if validation_result["valid"]:
                    self.github_creds_status_text.set("(Valid ✅)")
                    if hasattr(self, "github_creds_status_label"):
                        self.github_creds_status_label.config(foreground="green")
                else:
                    message = validation_result.get("message", "Invalid")
                    display_message = (
                        message[:20] + "..." if len(message) > 20 else message
                    )
                    self.github_creds_status_text.set(
                        f"(Invalid ❌: {display_message})"
                    )
                    if hasattr(self, "github_creds_status_label"):
                        self.github_creds_status_label.config(foreground="red")
                    self.root.after(
                        0, self.add_status, f"🔑 GitHub Credentials: {message}"
                    )
            except Exception as e:
                global_logger_ref.error(
                    f"Exception during GitHub validation thread: {e}", exc_info=True
                )
                self.github_creds_status_text.set("(Validation Error)")
                if hasattr(self, "github_creds_status_label"):
                    self.github_creds_status_label.config(foreground="red")
                self.root.after(
                    0, self.add_status, f"🔑 GitHub validation error: {str(e)}"
                )
            finally:
                self.root.after(0, self.update_credentials_display)
                loop.close()
        threading.Thread(target=validation_task, daemon=True).start()

    def setup_controller(self):
        try:
            from io_layer.system_controller import SystemController
            self.controller = SystemController()
            self.add_status("✅ System controller initialized")
            global_logger_ref.info("SystemController initialized successfully.")
        except Exception as e:
            self.add_status(f"❌ Failed to initialize controller: {str(e)}")
            global_logger_ref.error(f"Failed to initialize SystemController: {e}", exc_info=True)
            self.controller = None

    def _get_or_create_controller_with_credentials(self):
        global_logger_ref.debug("_get_or_create_controller_with_credentials called")
        try:
            from io_layer.system_controller import SystemController
            return SystemController(
                github_token=self.github_token.get(),
                github_username=self.github_username.get(),
                gemini_api_key=self.gemini_api_key.get(),
            )
        except Exception as e:
            global_logger_ref.error(
                f"Failed to create SystemController with credentials: {e}",
                exc_info=True,
            )
            raise

    def add_status(self, message):
        timestamp = time.strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}\n"
        self.status_text.config(state=tk.NORMAL)
        self.status_text.insert(tk.END, formatted_message)
        self.status_text.config(state=tk.DISABLED)
        self.status_text.see(tk.END)
        self.root.update_idletasks()

    def browse_repository(self):
        try:
            self.add_status("📂 Opening file browser...")
            browser = NativeFileBrowser()
            selected_path = browser.browse_for_directory(
                title="Select Repository Directory"
            )
            browser.close()
            if selected_path:
                self.current_repo_path.set(selected_path)
                self.add_status(f"✅ Repository selected: {selected_path}")
                self.validate_repository(selected_path)
        except Exception as e:
            self.add_status(f"❌ File browser error: {str(e)}")
            messagebox.showerror("Error", f"Failed to open file browser:\n{str(e)}")

    def validate_repository(self, path):
        try:
            from io_layer.file_browser import FileBrowser
            browser = FileBrowser()
            validation = browser.validate_repository_path(path)
            if validation["valid"]:
                self.add_status("✅ Repository validation passed")
                if validation["is_git_repo"]: self.add_status("🐙 Git repository detected")
                if validation["latest_commit"]:
                    commit_info = validation["latest_commit"]
                    commit_message = commit_info.get("message", "N/A").split("\n")[0]
                    author = commit_info.get("author_name", "N/A")
                    self.add_status(f' Commit: "{commit_message}" by {author}')
                if validation["warnings"]:
                    for warning in validation["warnings"]: self.add_status(f"⚠️ Warning: {warning}")
            else:
                self.add_status("❌ Repository validation failed")
                for error in validation["errors"]: self.add_status(f"❌ Error: {error}")
        except Exception as e:
            self.add_status(f"❌ Validation error: {str(e)}")

    def submit_feature(self):
        repo_path = self.current_repo_path.get()
        feature_spec = self.feature_text.get("1.0", tk.END).strip()

        if not repo_path:
            messagebox.showerror("Error", "Please select a project folder first")
            return
        if not feature_spec:
            messagebox.showerror("Error", "Please enter a feature specification")
            return
        creds_valid, missing_creds = self.validate_credentials()
        if not creds_valid:
            messagebox.showerror(
                "Missing Credentials",
                f"Please configure the following credentials:\n• {chr(10).join(missing_creds)}",
            )
            return

        self.add_status("🚀 Submitting feature request...")
        self.add_status(f"📁 Project: {repo_path}")
        self.add_status(f"✨ Feature: {feature_spec[:100]}...")
        self.add_status("🔑 Credentials validated")
        self.submit_btn.config(state="disabled", text="⏳ Processing...")
        threading.Thread(
            target=self._process_feature, args=(repo_path, feature_spec), daemon=True
        ).start()

    def _process_feature(self, repo_path, feature_spec):
        try:
            controller_with_creds = self._get_or_create_controller_with_credentials()
            self.add_status("🔧 Starting agent coordinator...")
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(controller_with_creds.start_system())
            self.add_status("✅ Agents started")
            self.add_status("📝 Submitting task...")
            task_id = loop.run_until_complete(
                controller_with_creds.submit_feature_task(repo_path, feature_spec)
            )
            global_logger_ref.info(f"Task submitted with ID: {task_id}")
            self.add_status(f"✅ Task submitted with ID: {task_id}")
            self.add_status("🔍 Task processing started...")
            self._monitor_task_progress(loop, task_id, controller_with_creds)
        except Exception as e:
            self.add_status(f"❌ Feature processing failed: {str(e)}")
            import traceback
            self.add_status(f"❌ Details: {traceback.format_exc()}")
        finally:
            self.root.after(
                0,
                lambda: self.submit_btn.config(
                    state="normal", text="🚀 Submit Feature Request"
                ),
            )

    async def _stream_task_events(self, task_id, controller=None):
        controller_to_use = controller or self.controller
        last_summary_status_msg = ""
        async for event in controller_to_use.progress_publisher.subscribe_to_progress(task_id):
            if event.message:
                self.root.after(0, self.add_status, event.message)
            summary = await controller_to_use.get_task_status(task_id)
            if summary:
                status_val = summary.status if hasattr(summary, "status") else "N/A"
                current_phase_val = summary.current_phase if hasattr(summary, "current_phase") else ""
                # progress_val = summary.progress_percentage if hasattr(summary, "progress_percentage") and summary.progress_percentage is not None else 0.0
                current_summary_status_msg = f"📊 Overall: {status_val} ({current_phase_val})"
                if current_summary_status_msg != last_summary_status_msg:
                    self.root.after(0, self.add_status, current_summary_status_msg)
                    last_summary_status_msg = current_summary_status_msg
            if event.event_type in [
                ProgressEventType.TASK_COMPLETED, ProgressEventType.TASK_FAILED, ProgressEventType.TASK_CANCELLED,
            ]:
                final_message = f"🏁 Task {event.event_type.value}"
                if event.data and event.data.get("error"):
                    final_message += f" - Error: {event.data.get('error')}"
                self.root.after(0, self.add_status, final_message)
                if summary and summary.status == "failed" and summary.error_message:
                    if not (event.data and event.data.get("error")):
                        self.root.after(0, self.add_status, f"📋 Details: {summary.error_message}")
                break

    def _monitor_task_progress(self, loop, task_id, controller_for_task):
        try:
            loop.run_until_complete(self._stream_task_events(task_id, controller=controller_for_task))
        except Exception as e:
            self.root.after(0, self.add_status, f"❌ Progress monitoring error: {str(e)}")
            import traceback
            self.root.after(0, self.add_status, f"❌ Details: {traceback.format_exc()}")

    def load_credentials(self):
        try:
            config_file = Path.home() / ".automaton_config.json"
            if config_file.exists():
                with open(config_file, "r") as f: config = json.load(f)
                self.github_token.set(config.get("github_token", ""))
                self.github_username.set(config.get("github_username", ""))
                self.gemini_api_key.set(config.get("gemini_api_key", ""))
                self.add_status("✅ Credentials loaded from file")
            else:
                self.github_token.set(os.getenv("GITHUB_TOKEN", ""))
                self.github_username.set(os.getenv("GITHUB_USERNAME", ""))
                self.gemini_api_key.set(os.getenv("GEMINI_API_KEY", ""))
                if any([self.github_token.get(), self.github_username.get(), self.gemini_api_key.get()]):
                    self.add_status("✅ Credentials loaded from environment variables")
                else:
                    self.add_status("⚠️ No credentials found - please configure them")
        except Exception as e:
            self.add_status(f"❌ Failed to load credentials: {str(e)}")
        self.update_credentials_display()
        self._trigger_initial_gemini_validation()
        self._trigger_initial_github_validation()

    def save_credentials(self):
        try:
            config = {
                "github_token": self.github_token.get(),
                "github_username": self.github_username.get(),
                "gemini_api_key": self.gemini_api_key.get(),
            }
            config_file = Path.home() / ".automaton_config.json"
            with open(config_file, "w") as f: json.dump(config, f, indent=2)
            os.environ["GITHUB_TOKEN"] = self.github_token.get()
            os.environ["GITHUB_USERNAME"] = self.github_username.get()
            os.environ["GEMINI_API_KEY"] = self.gemini_api_key.get()
            self.add_status("✅ Credentials saved successfully")
            messagebox.showinfo("Success", "Credentials saved successfully!")
            saved_gemini_key = self.gemini_api_key.get()
            if saved_gemini_key: self._validate_gemini_key_and_update_ui(saved_gemini_key)
            elif hasattr(self, "gemini_key_status_label"):
                self.gemini_key_status_label.config(text="(Not Set)", foreground="gray")
            saved_github_token = self.github_token.get()
            saved_github_username = self.github_username.get()
            if saved_github_token and saved_github_username:
                self._validate_github_credentials_and_update_ui(saved_github_token, saved_github_username)
            else:
                self.github_creds_status_text.set("(Not Set)")
                if hasattr(self, "github_creds_status_label"):
                    self.github_creds_status_label.config(foreground="gray")
                self.update_credentials_display()
        except Exception as e:
            self.add_status(f"❌ Failed to save credentials: {str(e)}")
            global_logger_ref.error(f"Failed to save credentials: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to save credentials:\n{str(e)}")

    def validate_credentials(self):
        missing = []
        if not self.github_token.get(): missing.append("GitHub Token")
        if not self.github_username.get(): missing.append("GitHub Username")
        if not self.gemini_api_key.get(): missing.append("Gemini API Key")
        is_valid = len(missing) == 0
        global_logger_ref.debug(f"Credentials validation: valid={is_valid}, missing={missing if not is_valid else 'None'}")
        return is_valid, missing

    def toggle_credentials(self):
        self.creds_expanded.set(not self.creds_expanded.get())
        self.update_credentials_display()

    def update_credentials_display(self):
        if self.creds_expanded.get():
            self.creds_frame.pack(fill="x", pady=(10, 0))
            self.toggle_creds_btn.config(text="🔑 Configure Credentials ▼")
        else:
            self.creds_frame.pack_forget()
            self.toggle_creds_btn.config(text="🔑 Configure Credentials ▶")
        creds_valid, missing = self.validate_credentials()
        if creds_valid: self.creds_status_label.config(text="✅ Configured", foreground="green")
        else: self.creds_status_label.config(text="⚠️ Not Configured", foreground="orange")

    def show_settings(self):
        def on_settings_changed(selected_model):
            self.add_status(f"⚙️ Model changed to: {selected_model}")
        settings_dialog = SettingsDialog(self.root, on_settings_changed)
        settings_dialog.show()

    def on_closing(self):
        result = messagebox.askyesno("Close Application", "Are you sure you want to close the application?")
        if result:
            self.root.quit()
            self.root.destroy()

    def run(self):
        global_logger_ref.info("🚀 Starting Automaton Desktop App...")
        self.root.mainloop()

def main():
    try:
        app = LLMAgentDesktopApp()
        app.run()
    except KeyboardInterrupt:
        print("--- KEYBOARD INTERRUPT CAUGHT IN main() ---", file=sys.stderr); sys.stderr.flush()
        if 'global_logger_ref' in globals() and global_logger_ref:
            global_logger_ref.info("Application terminated by KeyboardInterrupt in main().")
    except Exception as e_main:
        print(f"--- UNCAUGHT EXCEPTION IN main(): {type(e_main).__name__}: {e_main} ---", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        if 'global_logger_ref' in globals() and global_logger_ref:
            global_logger_ref.critical(f"UNCAUGHT EXCEPTION IN main(): {type(e_main).__name__}: {e_main}", exc_info=True)
        else:
            print("--- CRITICAL: global_logger_ref not available for final exception logging ---", file=sys.stderr)

if __name__ == "__main__":
    main()
