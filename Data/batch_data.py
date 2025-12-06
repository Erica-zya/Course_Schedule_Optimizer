import os
from generate_input import LargeScaleInputGenerator

def batch_produce(count=50, output_dir="batch_output"):
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Start generating {count} dataset...")

    for i in range(1, count + 1):
        current_seed = 1000 + i
        
        generator = LargeScaleInputGenerator(seed=current_seed)
        
        input_data = generator.generate_complete_input(
            num_courses=50,
            num_instructors=30,
            num_rooms=20,
            num_students=1000,
            num_weeks=14
        )
        
        filename = os.path.join(output_dir, f"schedule_input_{i:03d}.json")
        
        generator.save_to_file(input_data, filename)
        print(f"[{i}/{count}] 已保存: {filename}")

if __name__ == "__main__":
    batch_produce(50)