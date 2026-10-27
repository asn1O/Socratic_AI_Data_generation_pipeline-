import os
import json
from pathlib import Path

def count_json_entries(data_dir):
    data_path = Path(data_dir)
    exclude_folders = {'logs', 'rejected_responses'}
    
    total_count = 0
    folder_counts = {}
    
    if not data_path.exists():
        print(f"Data directory not found: {data_dir}")
        return
        
    for item in data_path.iterdir():
        if item.is_dir() and item.name not in exclude_folders:
            folder_count = 0
            for json_file in item.glob('*.json'):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            folder_count += len(data)
                        else:
                            print(f"Warning: {json_file} does not contain a JSON array.")
                except json.JSONDecodeError:
                    print(f"Error decoding JSON in {json_file}")
                except Exception as e:
                    print(f"Error reading {json_file}: {e}")
            
            folder_counts[item.name] = folder_count
            total_count += folder_count
            
    print("Counts per folder:")
    for folder, count in folder_counts.items():
        print(f"  {folder}: {count}")
        
    print(f"\nTotal count across all folders: {total_count}")

if __name__ == '__main__':
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / 'data'
    
    count_json_entries(data_dir)
