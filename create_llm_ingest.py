#!/usr/bin/env python3
import os

def generate_llm_ingest():
    base_dir = "."
    output_file = "codebase_ingest.txt"
    
    if os.path.exists(output_file):
        os.remove(output_file)
        print(f"Deleted old {output_file}")
    
    # Focus on the core bot directories and files
    include_dirs = ['src', 'strategies', 'config']
    include_root_files = ['main.py', 'README.md', 'Makefile', 'bot-context.md', 'PURE_SPREAD_ARB_PRD.md']
    
    # Exclude typical noise
    exclude_dirs = {'.venv', '__pycache__', '.git', '.gemini', 'logs', 'data'}
    
    with open(output_file, 'w', encoding='utf-8') as out:
        # First, process root files
        for f in include_root_files:
            path = os.path.join(base_dir, f)
            if os.path.isfile(path):
                _append_file(path, f, out)
                
        # Then walk the include directories
        for d in include_dirs:
            dir_path = os.path.join(base_dir, d)
            if not os.path.isdir(dir_path):
                continue
                
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                
                for f in sorted(files):
                    # Only include relevant source code and config extensions
                    if not f.endswith(('.py', '.yaml', '.json')):
                        continue
                    
                    # Ignore empty or trivial __init__.py files
                    path = os.path.join(root, f)
                    if f == '__init__.py' and os.path.getsize(path) < 50:
                        continue
                        
                    rel_path = os.path.relpath(path, base_dir)
                    _append_file(path, rel_path, out)

def _append_file(path, rel_path, out_file):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        out_file.write("================================================\n")
        out_file.write(f"FILE: {rel_path}\n")
        out_file.write("================================================\n")
        out_file.write(content)
        out_file.write("\n\n\n")
        print(f"Added {rel_path}")
    except Exception as e:
        print(f"Skipped {rel_path}: {e}")

if __name__ == "__main__":
    generate_llm_ingest()
    print("\nDone! Created codebase_ingest.txt")
