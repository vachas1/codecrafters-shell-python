import sys
import os
import subprocess
import readline
import io
from contextlib import redirect_stdout

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Commands that are handled directly by this shell (not looked up on disk).
BUILTINS = ["echo", "type", "exit", "pwd", "cd", "history"]

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

# Every command the user types is stored here so 'history' can display it.
command_history = []

# Tracks how many history entries existed at the start of this session.
# Used by 'history -a' to only append NEW commands (not re-append old ones).
last_synced_index = 0


# ---------------------------------------------------------------------------
# History File — Load & Save
# ---------------------------------------------------------------------------

def load_history_from_file():
    """
    Load commands from $HISTFILE into memory when the shell starts.
    This lets the user see and re-run commands from previous sessions.
    """
    global last_synced_index

    histfile = os.environ.get("HISTFILE")
    if not histfile or not os.path.exists(histfile):
        return  # $HISTFILE not set or doesn't exist yet — nothing to load

    try:
        with open(histfile, "r") as f:
            for line in f:
                clean = line.strip()
                if clean:
                    command_history.append(clean)

        # Mark where the file's history ends so we can track new commands separately
        last_synced_index = len(command_history)

    except Exception:
        pass  # Don't crash if the file can't be read


def save_history_to_file():
    """
    Write all in-memory commands to $HISTFILE when the shell exits.
    This makes history persist across sessions.
    """
    histfile = os.environ.get("HISTFILE")
    if not histfile:
        return  # Nowhere to save

    try:
        os.makedirs(os.path.dirname(os.path.abspath(histfile)), exist_ok=True)
        with open(histfile, "w") as f:
            for cmd in command_history:
                f.write(cmd + "\n")
    except Exception:
        pass  # Don't crash if writing fails


# ---------------------------------------------------------------------------
# History Builtin — 'history' command logic
# ---------------------------------------------------------------------------

def history_functionality(args=None, output_file=None, fd=None):
    """
    Handle the 'history' builtin command.

    Flags:
      history -a <file>  → Append only NEW commands (since session start) to file
      history -r <file>  → Read commands from file into memory
      history -w <file>  → Write ALL commands to file (overwrite)
      history            → Print all commands
      history N          → Print last N commands
    """
    global last_synced_index

    if args:
        flag = args[0]

        # -a: Append only commands added in this session to the file
        if flag == "-a" and len(args) > 1:
            path = args[1]
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "a") as f:
                    for entry in command_history[last_synced_index:]:
                        f.write(entry + "\n")
                last_synced_index = len(command_history)
            except Exception:
                pass
            return

        # -r: Read commands from a file into memory
        if flag == "-r" and len(args) > 1:
            path = args[1]
            try:
                with open(path, "r") as f:
                    for line in f:
                        clean = line.strip()
                        if clean:
                            command_history.append(clean)
                last_synced_index = len(command_history)
            except FileNotFoundError:
                pass
            return

        # -w: Overwrite the file with all current history
        if flag == "-w" and len(args) > 1:
            path = args[1]
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w") as f:
                    for entry in command_history:
                        f.write(entry + "\n")
                last_synced_index = len(command_history)
            except Exception:
                pass
            return

    # No flag — display history (optionally limited to last N entries)
    limit = len(command_history)
    if args:
        try:
            limit = int(args[0])  # e.g. 'history 5' shows last 5 commands
        except ValueError:
            pass  # Not a number — show all

    start = max(0, len(command_history) - limit)
    lines = [f" {i + 1} {command_history[i]}" for i in range(start, len(command_history))]
    result = "\n".join(lines)

    if output_file:
        # Redirect output to a file instead of printing
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w") as f:
            f.write(result + "\n")
    elif result:
        print(result)


# ---------------------------------------------------------------------------
# Tab Completion
# ---------------------------------------------------------------------------

def get_command_matches(text):
    """
    Return all builtins and PATH executables that start with `text`.
    Used when completing the first word (the command name).
    """
    # Check builtins first
    matches = [cmd for cmd in BUILTINS if cmd.startswith(text)]

    # Then search every directory in $PATH for matching executables
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isdir(directory):
            continue
        try:
            for filename in os.listdir(directory):
                if filename.startswith(text):
                    full_path = os.path.join(directory, filename)
                    if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                        matches.append(filename)
        except PermissionError:
            continue  # Skip unreadable directories

    return sorted(list(set(matches)))  # Deduplicate and sort


def get_filename_matches(text):
    """
    Return all files in the current directory that start with `text`.
    Simple helper used for argument completion.
    """
    try:
        return [f for f in os.listdir('.') if f.startswith(text)]
    except Exception:
        return []


def complete_path(text):
    """
    Return file/directory completions for a partial path.

    Handles both:
      - Plain names:    'read'     → searches current directory
      - Nested paths:   'src/ma'   → searches inside 'src/'

    Directories get a trailing '/' to invite further completion.
    Files get a trailing ' ' so the user can immediately type the next argument.
    """
    if "/" in text:
        # Split at the last slash: directory part + filename prefix
        directory, prefix = text.rsplit("/", 1)
        search_dir = directory if directory else "/"
    else:
        # No slash — search in current directory
        search_dir = "."
        prefix = text
        directory = ""

    try:
        entries = os.listdir(search_dir)
    except (FileNotFoundError, PermissionError):
        return []

    matches = []
    for entry in entries:
        if not entry.startswith(prefix):
            continue
        # Reconstruct the full path (preserving any directory prefix the user typed)
        full = (directory + "/" + entry) if directory else entry
        if os.path.isdir(os.path.join(search_dir, entry)):
            matches.append(full + "/")   # Directory → trailing slash
        else:
            matches.append(full + " ")   # File → trailing space

    return sorted(matches)


def completer(text, state):
    """
    Readline completer function — called every time the user presses TAB.

    readline calls this repeatedly with state=0, 1, 2, ... until None is returned.
    Each call returns one possible completion.

    Strategy:
      - First word on the line  → complete command names (builtins + executables)
      - Any later word          → complete file/directory paths
    """
    buffer = readline.get_line_buffer()  # Full line typed so far
    parts = buffer.split()

    # Are we completing the command name?
    # True if: nothing typed yet, OR exactly one word with no trailing space
    completing_command = (len(parts) == 0) or (len(parts) == 1 and not buffer.endswith(" "))

    if completing_command:
        # Merge builtins and executables, deduplicate, sort
        builtin_matches = [cmd for cmd in BUILTINS if cmd.startswith(text)]
        exe_matches = get_command_matches(text)
        matches = sorted(set(builtin_matches + exe_matches))

        # Only add a trailing space if there's exactly one match
        # (multiple matches → readline shows them all, no space yet)
        if len(matches) == 1:
            matches = [matches[0] + " "]

    else:
        # Completing a filename argument — extract the partial path after the last space
        last_space_idx = buffer.rfind(" ")
        partial = buffer[last_space_idx + 1:]
        matches = complete_path(partial) or []

        # Strip trailing space if multiple matches (let readline show the list first)
        if len(matches) != 1:
            matches = [m.rstrip(" ") for m in matches]

    if state < len(matches):
        return matches[state]
    return None  # No more completions


def setup_readline():
    """
    Configure readline for tab completion.

    Key setting: set_completer_delims(' \\t\\n') removes characters like '-' and '.'
    from readline's word-break list. Without this, filenames like 'apple-7.txt'
    would be split at '-' and completion would break.
    """
    readline.set_completer_delims(' \t\n')  # Only split on whitespace
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


# ---------------------------------------------------------------------------
# Command Parsing
# ---------------------------------------------------------------------------

def parse_command(command):
    """
    Parse a raw shell command string into a list of argument tokens.

    Handles:
      Single quotes  ' '  → everything inside is literal (no escaping at all)
      Double quotes  " "  → mostly literal; backslash escapes: " \\ $ `
      Backslash outside quotes → escapes the very next character
      Spaces → separate tokens (unless inside quotes)

    Examples:
      'echo hello world'       → ['echo', 'hello', 'world']
      'echo "hello world"'     → ['echo', 'hello world']
      "echo it\\'s"            → ['echo', "it's"]
    """
    args = []
    current_arg = []   # Characters building the current token
    active_quote = None  # None = unquoted, "'" or '"' = inside that quote
    i = 0

    while i < len(command):
        char = command[i]

        if active_quote == "'":
            # Inside single quotes: only ' ends the section, everything else is literal
            if char == "'":
                active_quote = None
            else:
                current_arg.append(char)

        elif active_quote == '"':
            # Inside double quotes: backslash can escape special characters
            if char == "\\":
                if i + 1 < len(command):
                    next_char = command[i + 1]
                    if next_char in ('"', "\\", '$', '`'):
                        # These chars can be escaped inside double quotes
                        current_arg.append(next_char)
                        i += 1  # Skip the escaped character
                    else:
                        current_arg.append(char)  # Literal backslash
            elif char == '"':
                active_quote = None  # End of double-quoted section
            else:
                current_arg.append(char)

        else:
            # Outside any quotes — normal shell parsing
            if char == '\\':
                # Escape the next character literally
                if i + 1 < len(command):
                    i += 1
                    current_arg.append(command[i])
            elif char in ("'", '"'):
                active_quote = char  # Start a quoted section
            elif char == " ":
                # Space ends the current token
                if current_arg:
                    args.append("".join(current_arg))
                    current_arg = []
            else:
                current_arg.append(char)

        i += 1

    # Don't forget the last token (may not be followed by a space)
    if current_arg:
        args.append("".join(current_arg))

    return args


def handle_redirection(parts):
    """
    Scan the token list for redirection operators and extract them.

    Supported operators:
      >  or 1>   → redirect stdout, overwrite
      >> or 1>>  → redirect stdout, append
      2>          → redirect stderr, overwrite
      2>>         → redirect stderr, append

    Returns: (clean_parts, output_file, fd, mode)
      clean_parts  → tokens with the operator + filename removed
      output_file  → the filename to redirect to (or None)
      fd           → 1 = stdout, 2 = stderr (or None)
      mode         → 'w' = overwrite, 'a' = append
    """
    for i, part in enumerate(parts):
        if part in (">", "1>") and i + 1 < len(parts):
            return parts[:i], parts[i + 1], 1, "w"
        elif part in (">>", "1>>") and i + 1 < len(parts):
            return parts[:i], parts[i + 1], 1, "a"
        elif part == "2>" and i + 1 < len(parts):
            return parts[:i], parts[i + 1], 2, "w"
        elif part == "2>>" and i + 1 < len(parts):
            return parts[:i], parts[i + 1], 2, "a"

    # No redirection found
    return parts, None, None, "w"


# ---------------------------------------------------------------------------
# Executable Lookup
# ---------------------------------------------------------------------------

def find_executable(command):
    """
    Search $PATH for an executable named `command`.
    Returns the full path if found, or None if not found.

    This is how the shell locates programs like 'ls', 'grep', 'cat' etc.
    """
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        full_path = os.path.join(directory, command)
        if os.path.exists(full_path) and os.access(full_path, os.X_OK):
            return full_path
    return None


# ---------------------------------------------------------------------------
# Builtin Command Implementations
# ---------------------------------------------------------------------------

def echo_functionality(args, output_file=None, fd=None, mode="w"):
    """
    Implement 'echo': join all args with a space and print.
    Supports stdout redirection to a file.
    """
    output = " ".join(args)

    if output_file:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        if fd == 1:
            # Redirect stdout to file
            with open(output_file, mode) as f:
                f.write(output + "\n")
        else:
            # Stderr redirected — create the file but still print to terminal
            if not os.path.exists(output_file):
                open(output_file, "a").close()
            print(output)
    else:
        print(output)


def type_functionality(command, output_file=None, fd=None):
    """
    Implement 'type': tell the user whether a command is a builtin,
    an executable on PATH, or not found at all.

    Examples:
      type echo  →  'echo is a shell builtin'
      type ls    →  'ls is /bin/ls'
      type xyz   →  'xyz: not found'
    """
    if command in BUILTINS:
        result = f"{command} is a shell builtin"
    else:
        full_path = find_executable(command)
        result = f"{command} is {full_path}" if full_path else f"{command}: not found"

    if output_file:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        if fd == 1:
            with open(output_file, "w") as f:
                f.write(result + "\n")
        else:
            open(output_file, "w").close()
            print(result)
    else:
        print(result)


def get_current_working_directory(output_file=None, fd=None):
    """
    Implement 'pwd': print the current working directory.
    """
    cwd = os.getcwd()

    if output_file:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        if fd == 1:
            with open(output_file, "w") as f:
                f.write(cwd + "\n")
        else:
            open(output_file, "w").close()
            print(cwd)
    else:
        print(cwd)


def change_dir(path):
    """
    Implement 'cd': change the current working directory.
    '~' expands to the user's $HOME directory.
    """
    if path == "~":
        path = os.environ.get("HOME", "/")  # Fall back to root if $HOME not set
    try:
        os.chdir(path)
    except Exception:
        print(f"cd: {path}: No such file or directory")


# ---------------------------------------------------------------------------
# Pipeline Execution
# ---------------------------------------------------------------------------

def run_builtin_to_string(cmd, args):
    """
    Run a builtin command and capture its printed output as a string.

    This is needed when a builtin appears inside a pipeline —
    since builtins run inside Python (no subprocess), we capture their
    stdout using StringIO and pass it as input to the next pipe stage.
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        if cmd == "echo":
            echo_functionality(args)
        elif cmd == "type":
            type_functionality(args[0])
        elif cmd == "pwd":
            get_current_working_directory()
        elif cmd == "history":
            history_functionality(args)
    return buf.getvalue()


def executable_pipeline(command):
    """
    Execute a pipeline of commands connected by '|'.

    Each command's stdout is piped into the next command's stdin.
    The final command's output goes to the terminal.

    Special case: if a builtin appears in the pipeline, we capture its
    output manually (since we can't fork it as a subprocess) and feed
    it as bytes to the next stage.
    """
    # Split the full command at each '|' into individual segments
    segments = [seg.strip() for seg in command.split("|")]

    if len(segments) < 2:
        return False  # Not actually a pipeline

    prev_proc = None      # Previous subprocess (used to chain stdout → stdin)
    pending_input = None  # Buffered output from a builtin to pass to next stage

    for i, segment in enumerate(segments):
        args = parse_command(segment)
        is_last = (i == len(segments) - 1)

        if not args:
            continue

        if args[0] in BUILTINS:
            # Builtins can't be run as subprocesses — capture output manually
            output = run_builtin_to_string(args[0], args[1:])
            if is_last:
                print(output, end="")  # Last stage — print to terminal
                return True
            pending_input = output.encode()  # Pass output to next stage
            continue

        try:
            # Wire up stdin: either from buffered builtin output or previous process pipe
            stdin_val = subprocess.PIPE if pending_input else (prev_proc.stdout if prev_proc else None)

            # Wire up stdout: pipe it unless this is the last command
            stdout_val = subprocess.PIPE if not is_last else None

            proc = subprocess.Popen(args, stdin=stdin_val, stdout=stdout_val)

            if pending_input:
                # Send buffered data and collect output for the next stage
                stdout_data, _ = proc.communicate(input=pending_input)
                pending_input = stdout_data if not is_last else None

            if prev_proc:
                prev_proc.stdout.close()  # Close the read end in the parent process

            prev_proc = proc

        except Exception:
            print(f"{args[0]}: command not found")
            return False

    # Wait for the final process to finish
    if prev_proc and not pending_input:
        prev_proc.wait()

    return True


# ---------------------------------------------------------------------------
# Main REPL — Read, Evaluate, Print Loop
# ---------------------------------------------------------------------------

def main():
    """
    The main shell loop — runs forever until the user types 'exit' or Ctrl+D.

    Each iteration:
      1. Show '$ ' prompt and read a line
      2. Save command to history
      3. Handle pipelines, parse tokens, detect redirections
      4. Dispatch to the correct builtin or external command handler
    """
    setup_readline()          # Enable TAB completion
    load_history_from_file()  # Load history from previous session

    while True:
        try:
            # input() integrates with readline automatically.
            # readline handles up/down arrow history, line editing, and TAB.
            command = input("$ ")
        except EOFError:
            # Ctrl+D — exit gracefully
            save_history_to_file()
            break

        if not command.strip():
            continue  # Ignore empty lines

        # Record every command in our history list
        command_history.append(command)

        # Handle pipelines (commands joined with |) as a special case
        if "|" in command:
            executable_pipeline(command)
            continue

        # Parse the raw string into a list of tokens
        all_parts = parse_command(command)
        if not all_parts:
            continue

        # Separate any redirection operators from the command tokens
        parts, output_file, fd, mode = handle_redirection(all_parts)
        cmd = parts[0]    # The command name (first token)
        args = parts[1:]  # Everything after the command name

        # --- Dispatch to the right handler ---

        if cmd == "exit":
            save_history_to_file()
            break

        elif cmd == "echo":
            echo_functionality(args, output_file, fd, mode)

        elif cmd == "type":
            if args:
                type_functionality(args[0], output_file, fd)

        elif cmd == "pwd":
            get_current_working_directory(output_file, fd)

        elif cmd == "cd":
            if args:
                change_dir(args[0])

        elif cmd == "history":
            history_functionality(args, output_file, fd)

        else:
            # Not a builtin — look it up on $PATH and run it
            executable_path = find_executable(cmd)
            if executable_path:
                if output_file:
                    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
                    with open(output_file, mode) as f:
                        if fd == 2:
                            subprocess.run(parts, stderr=f)   # Redirect stderr
                        else:
                            subprocess.run(parts, stdout=f)   # Redirect stdout
                else:
                    subprocess.run(parts)  # Normal run — output to terminal
            else:
                # Command not found — print error (possibly to a redirected file)
                error_msg = f"{cmd}: command not found"
                if output_file and fd == 2:
                    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
                    with open(output_file, mode) as f:
                        f.write(error_msg + "\n")
                else:
                    print(error_msg)


if __name__ == "__main__":
    main()
