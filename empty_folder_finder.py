"""
VoidFinder

Description:
    A cross-platform GUI utility engineered to identify and safely remove empty directories.
    The tool intelligently ignores common system-generated files (e.g., macOS's
    `.DS_Store` or Windows' `Thumbs.db`), allowing it to find folders that appear
    empty to the user but are not technically empty on the filesystem. It provides a
    robust, user-friendly interface for disk cleanup, featuring a non-blocking
    progress window with a cancellation option for long-running scans.

Operational Workflow:
  1.  The application launches a Tkinter-based graphical user interface (GUI).
  2.  The user selects a root directory for scanning via a native file dialog.
  3.  The scan operation is initiated on a separate, non-blocking worker thread
      to maintain GUI responsiveness.
  4.  A progress window is displayed, showing detailed status updates and a
      progress bar. The user can close this window to gracefully cancel the scan.
  5.  The worker thread first executes a pre-scan to enumerate all subdirectories,
      enabling an accurate progress meter.
  6.  It then performs the main recursive scan, checking a cancellation flag between
      directory operations to ensure prompt termination if requested.
  7.  A folder's path is collected if it contains no subdirectories and no files,
      or only files designated as ignorable.
  8.  Upon completion or cancellation, the progress window is closed, and the
      results are populated in the main listbox.
  9.  The user can select single or multiple folders from the results list. Native
      macOS key bindings (Command-Click) are supported for multi-selection.
 10. A status panel provides detailed information for the selected folder, including
     its full path, status, content size, and a list of any ignored files within it.
 11. The user may open the selected folder in the system's file manager or, after
     a confirmation prompt, move the selected items to the system Trash.
 12. A final summary report, presented in a read-only but copyable window,
     details the outcome of the move operation. This report is also logged to the
     console.

Usage:
    - Ensure required libraries are installed:
          pip install send2trash
    - Run the script from a terminal or by executing the file directly:
          python empty_folder_finder.py

Author:     Vitalii Starosta
GitHub:     https://github.com/sztaroszta
License:    GNU Affero General Public License v3 (AGPLv3)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import subprocess
import send2trash
import sys
import threading
import queue

# A list of system files/folders to ignore when determining if a directory is empty.
IGNORED_ITEMS = ['.DS_Store', 'Thumbs.db', 'desktop.ini']

def get_folder_size(folder_path):
    """Calculates the total size of all files within a directory tree."""
    total_size = 0
    try:
        for dirpath, _, filenames in os.walk(folder_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    try:
                        total_size += os.path.getsize(fp)
                    except OSError:
                        pass
    except OSError:
        pass
    return total_size

def format_size(size_in_bytes):
    """Converts a size in bytes to a human-readable string (KB, MB, GB)."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes} Bytes"
    elif size_in_bytes < 1024**2:
        return f"{size_in_bytes / 1024:.2f} KB"
    elif size_in_bytes < 1024**3:
        return f"{size_in_bytes / 1024**2:.2f} MB"
    else:
        return f"{size_in_bytes / 1024**3:.2f} GB"

def select_directory():
    """GUI callback to open a directory selection dialog and initiate the scan thread."""
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        entry_path.delete(0, tk.END)
        entry_path.insert(0, folder_selected)
        start_scan_thread(folder_selected)

def scan_thread_worker(folder_path, q, cancel_event):
    """
    The worker function that scans for empty folders in a separate thread.
    It can be stopped early via the `cancel_event`.
    """
    try:
        # 1. Pre-scan to count directories for the progress bar
        q.put(('status', 'Pre-scanning to count items...'))
        total_dirs = 0
        for _, dirs, _ in os.walk(folder_path):
            if cancel_event.is_set():
                print("\nScan cancelled during pre-scan.")
                q.put(('cancelled', None))
                return
            total_dirs += len(dirs)
        q.put(('max', total_dirs))
        
        # 2. Main scan to find empty folders
        empty_folders = []
        processed_dirs = 0
        for root, dirs, files in os.walk(folder_path, topdown=False):
            if cancel_event.is_set():
                print("\nScan cancelled by user.")
                q.put(('cancelled', None))
                return

            processed_dirs += 1
            non_ignored_files = [f for f in files if f not in IGNORED_ITEMS]
            
            if not dirs and not non_ignored_files:
                empty_folders.append(root)

            if processed_dirs % 500 == 0: # Update less frequently to improve performance
                q.put(('progress', processed_dirs))
                q.put(('status', f'Scanning:\n{root}'))
                print(f"[{processed_dirs}/{total_dirs}] Scanning: {root}", end='\r', flush=True)

        print(f"\nScan complete. Found {len(empty_folders)} empty folders.")
        q.put(('done', empty_folders))
    except Exception as e:
        q.put(('error', str(e)))

def start_scan_thread(folder_path):
    """Initializes and starts the folder scanning thread and progress window."""
    listbox_empty_folders.delete(0, tk.END)
    label_status.config(text="Searching for empty folders...", fg="blue")
    button_browse.config(state=tk.DISABLED)

    progress_win = tk.Toplevel(root)
    progress_win.title("Scanning...")
    progress_win.transient(root)
    progress_win.grab_set()
    progress_win.geometry("450x120")
    
    status_label = tk.Label(progress_win, text="Initializing scan...", justify=tk.LEFT, anchor="w")
    status_label.pack(pady=10, padx=10, fill=tk.X)
    
    progress_bar = ttk.Progressbar(progress_win, orient='horizontal', mode='determinate')
    progress_bar.pack(pady=10, padx=10, fill=tk.X, expand=True)
    
    q = queue.Queue()
    cancel_event = threading.Event()

    def on_cancel_scan():
        """Function called when the progress window is closed."""
        cancel_event.set()  # Signal the thread to stop
        progress_win.destroy()
        label_status.config(text="Scan cancelled by user.")
        button_browse.config(state=tk.NORMAL)
        
    # Intercept the window close ('X') button
    progress_win.protocol("WM_DELETE_WINDOW", on_cancel_scan)

    thread = threading.Thread(target=scan_thread_worker, args=(folder_path, q, cancel_event))
    thread.daemon = True
    thread.start()
    
    process_queue(q, progress_bar, status_label, progress_win, cancel_event)

def process_queue(q, progress_bar, status_label, progress_win, cancel_event):
    """Periodically checks the queue for messages from the worker thread."""
    # If the cancel event was set by the user, stop processing the queue
    if cancel_event.is_set():
        return

    try:
        msg_type, value = q.get_nowait()
        if msg_type == 'max':
            progress_bar['maximum'] = value if value > 0 else 1
        elif msg_type == 'progress':
            progress_bar['value'] = value
        elif msg_type == 'status':
            status_label.config(text=value)
        elif msg_type == 'done':
            progress_win.destroy()
            populate_results_in_listbox(value)
            return
        elif msg_type == 'cancelled':
            # The worker acknowledged the cancellation, so we can stop.
            return
        elif msg_type == 'error':
            progress_win.destroy()
            messagebox.showerror("Error", f"An error occurred during scanning:\n{value}")
            populate_results_in_listbox([])
            return
    except queue.Empty:
        pass
    finally:
        root.after(100, process_queue, q, progress_bar, status_label, progress_win, cancel_event)

def populate_results_in_listbox(empty_folders):
    """Populates the listbox with scan results and re-enables UI elements."""
    listbox_empty_folders.delete(0, tk.END)
    if empty_folders:
        for folder in sorted(empty_folders):
            listbox_empty_folders.insert(tk.END, folder)
        label_status.config(text=f"Found {len(empty_folders)} empty folders.", fg="black")
    else:
        label_status.config(text="No empty folders found.", fg="black")
    
    button_browse.config(state=tk.NORMAL)

def open_selected_folder():
    """GUI callback to open the selected folder in the native file manager."""
    selected_indices = listbox_empty_folders.curselection()
    if selected_indices:
        selected_folder_path = listbox_empty_folders.get(selected_indices[0])
        try:
            if sys.platform == "win32":
                subprocess.run(['explorer', selected_folder_path], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            elif sys.platform == "darwin":
                subprocess.run(['open', selected_folder_path], check=True)
            else:
                subprocess.run(['xdg-open', selected_folder_path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the folder: {e}")
    else:
        messagebox.showwarning("Warning", "Please select a folder from the list.")

def show_summary_report_window(summary_text):
    """
    Displays a non-editable, but selectable/copyable summary report in a Toplevel window.
    """
    summary_win = tk.Toplevel(root)
    summary_win.title("Move Summary")
    summary_win.geometry("650x400")  # Give it a reasonable default size
    summary_win.transient(root)  # Keep it on top of the main window
    summary_win.grab_set()  # Make it modal

    # Main frame to hold the text widget and scrollbar
    text_frame = tk.Frame(summary_win)
    text_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

    # Create and pack the scrollbar first
    scrollbar = tk.Scrollbar(text_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Create the Text widget
    summary_text_widget = tk.Text(
        text_frame, 
        wrap=tk.WORD, 
        yscrollcommand=scrollbar.set,
        padx=5, 
        pady=5,
        relief=tk.FLAT, # Use a flat relief to blend with the window
        background=summary_win.cget('bg') # Match window background
    )
    summary_text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    
    # Configure the scrollbar to control the text widget
    scrollbar.config(command=summary_text_widget.yview)

    # Insert the summary text
    summary_text_widget.insert(tk.END, summary_text)
    
    # IMPORTANT: Disable writing to the widget after inserting text
    summary_text_widget.config(state=tk.DISABLED)

    # Add a close button
    close_button = tk.Button(summary_win, text="Close", command=summary_win.destroy)
    close_button.pack(pady=(0, 10))

    # Center the summary window on the root window
    summary_win.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() // 2) - (summary_win.winfo_width() // 2)
    y = root.winfo_y() + (root.winfo_height() // 2) - (summary_win.winfo_height() // 2)
    summary_win.geometry(f"+{x}+{y}")
    
    # Wait for the user to close the summary window before continuing
    root.wait_window(summary_win)

def move_selected_to_trash():
    """
    GUI callback to move selected folders to the Trash.
    It shows a final summary in a new, non-editable but copyable window.
    """
    selected_indices = listbox_empty_folders.curselection()
    if not selected_indices:
        messagebox.showwarning("Warning", "Please select folders to move.")
        return

    folders_to_trash = [listbox_empty_folders.get(i) for i in selected_indices]
    
    prompt = f"Are you sure you want to move {len(folders_to_trash)} folder(s) to the Trash?"
    if messagebox.askyesno("Confirmation", prompt):
        successful_moves, failed_moves = [], []
        for folder_path in folders_to_trash:
            try:
                # Re-verify the folder is still empty before deleting
                if not os.path.exists(folder_path):
                    raise FileNotFoundError("Folder not found (already moved or deleted).")
                
                current_contents = os.listdir(folder_path)
                non_ignored_contents = [item for item in current_contents if item not in IGNORED_ITEMS]

                if not non_ignored_contents:
                    send2trash.send2trash(folder_path)
                    successful_moves.append(folder_path)
                else:
                    # The folder is no longer empty
                    failed_moves.append((folder_path, "No longer empty."))
            except Exception as e:
                failed_moves.append((folder_path, str(e)))

        # --- Build Summary Text ---
        summary_lines = []
        summary_lines.append("=" * 50)
        summary_lines.append("Move to Trash Summary")
        summary_lines.append("-" * 50)
        
        if successful_moves:
            summary_lines.append(f"Successfully moved {len(successful_moves)} folder(s) to Trash:")
            for folder in successful_moves:
                summary_lines.append(f"  - {folder}")
        
        if failed_moves:
            # Add a newline for better separation if there were also successful moves
            if successful_moves:
                summary_lines.append("") 
            summary_lines.append(f"Failed to move {len(failed_moves)} folder(s):")
            for path, reason in failed_moves:
                summary_lines.append(f"  - {path}: {reason}")
        
        if not successful_moves and not failed_moves:
            summary_lines.append("No folders were moved.")

        summary_lines.append("=" * 50)
        final_summary_text = "\n".join(summary_lines)

        # Print to console for logging purposes
        print("\n" + final_summary_text)

        # Show the summary in a dedicated, read-only window
        show_summary_report_window(final_summary_text)

        # Refresh the main listbox to reflect the changes
        current_search_path = entry_path.get()
        if current_search_path:
            start_scan_thread(current_search_path)


def on_folder_select(event):
    """Event handler to update the side panel with details of the selected folder."""
    selected_indices = listbox_empty_folders.curselection()
    label_folder_status.config(state=tk.NORMAL)
    label_folder_status.delete('1.0', tk.END)

    if selected_indices:
        selected_folder_path = listbox_empty_folders.get(selected_indices[0])
        folder_size_formatted = format_size(get_folder_size(selected_folder_path))

        try:
            if os.path.exists(selected_folder_path):
                contents = os.listdir(selected_folder_path)
                non_ignored = [item for item in contents if item not in IGNORED_ITEMS]
                ignored = [item for item in contents if item in IGNORED_ITEMS]
                label_folder_status.insert(tk.END, f"Folder:\n{selected_folder_path}\n\n")
                label_folder_status.insert(tk.END, "Status: EMPTY\n" if not non_ignored else "Status: NOT EMPTY!\n", "green" if not non_ignored else "red")
                label_folder_status.insert(tk.END, f"Size: {folder_size_formatted}")
                if ignored:
                    label_folder_status.insert(tk.END, f"\n\nContains ignored items: {', '.join(ignored)}", "orange")
            else:
                label_folder_status.insert(tk.END, "Status: Not found.", "gray")
        except Exception as e:
            label_folder_status.insert(tk.END, f"Status: Error checking: {e}", "red")

    label_folder_status.tag_config("green", foreground="green")
    label_folder_status.tag_config("red", foreground="red")
    label_folder_status.tag_config("gray", foreground="gray")
    label_folder_status.tag_config("orange", foreground="orange")
    label_folder_status.config(state=tk.DISABLED)

# --- GUI Setup ---
if __name__ == "__main__":
    """The main entry point for the script."""
    print("VoidFinder")
    print("=" * 40)
    
    root = tk.Tk()
    root.title("VoidFinder - Empty Folder Finder")

    main_frame = tk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    frame_select_dir = tk.Frame(main_frame)
    frame_select_dir.grid(row=0, column=0, columnspan=2, pady=(0, 10), sticky="ew")
    tk.Label(frame_select_dir, text="Directory:").pack(side=tk.LEFT)
    entry_path = tk.Entry(frame_select_dir, width=60)
    entry_path.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
    button_browse = tk.Button(frame_select_dir, text="Browse...", command=select_directory)
    button_browse.pack(side=tk.LEFT)

    frame_list = tk.Frame(main_frame)
    frame_list.grid(row=1, column=0, sticky="nsew")
    scrollbar = tk.Scrollbar(frame_list)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    listbox_empty_folders = tk.Listbox(frame_list, width=80, height=15, yscrollcommand=scrollbar.set, selectmode=tk.EXTENDED)
    listbox_empty_folders.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=listbox_empty_folders.yview)
    listbox_empty_folders.bind('<<ListboxSelect>>', on_folder_select)

    # Bind native macOS Command-key combinations for intuitive multi-selection.
    if sys.platform == "darwin":
        def command_click_select(event):
            clicked_index = listbox_empty_folders.nearest(event.y)
            if listbox_empty_folders.selection_includes(clicked_index):
                listbox_empty_folders.selection_clear(clicked_index)
            else:
                listbox_empty_folders.selection_set(clicked_index)
            return "break"
        def command_shift_click_select(event):
            anchor_index = listbox_empty_folders.index("anchor")
            active_index = listbox_empty_folders.nearest(event.y)
            listbox_empty_folders.selection_clear(0, tk.END)
            listbox_empty_folders.selection_set(anchor_index, active_index)
            return "break"
        listbox_empty_folders.bind('<Command-1>', command_click_select)
        listbox_empty_folders.bind('<Command-Shift-1>', command_shift_click_select)

    frame_actions = tk.Frame(main_frame)
    frame_actions.grid(row=2, column=0, pady=(10, 0), sticky="ew")
    button_open = tk.Button(frame_actions, text="Open Folder", command=open_selected_folder)
    button_open.pack(side=tk.LEFT, padx=(0, 5), expand=True, fill=tk.X)
    button_trash = tk.Button(frame_actions, text="Move to Trash", command=move_selected_to_trash)
    button_trash.pack(side=tk.LEFT, padx=(5, 0), expand=True, fill=tk.X)

    frame_preview = tk.LabelFrame(main_frame, text="Selected Folder Status")
    frame_preview.grid(row=1, column=1, sticky="nsew", padx=(10, 0), rowspan=2)
    label_folder_status = tk.Text(frame_preview, wrap=tk.WORD, height=10, width=40, padx=5, pady=5)
    label_folder_status.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
    label_folder_status.config(state=tk.DISABLED, background=root.cget('bg'), relief=tk.FLAT)

    label_status = tk.Label(root, text="Select a directory to begin.")
    label_status.pack(pady=5)

    main_frame.grid_columnconfigure(0, weight=3)
    main_frame.grid_columnconfigure(1, weight=2)
    main_frame.grid_rowconfigure(1, weight=1)

    root.mainloop()