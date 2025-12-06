include("course_scheduler.jl")

using JSON

# Recursive function to convert JSON.Object to Dict
function json_to_dict(obj)
    if isa(obj, JSON.Object)
        result = Dict{String, Any}()
        for (key, value) in obj
            result[string(key)] = json_to_dict(value)
        end
        return result
    elseif isa(obj, Array)
        return [json_to_dict(item) for item in obj]
    else
        return obj
    end
end

function main()
    if length(ARGS) < 1
        println("Usage: julia cli_runner.jl <input_json_file>")
        exit(1)
    end

    input_file = ARGS[1]
    
    if !isfile(input_file)
        println("❌ Error: Input file '$input_file' not found.")
        exit(1)
    end

    println("🚀 Loading data from: $input_file")
    json_obj = JSON.parsefile(input_file)
    
    # Convert JSON.Object to Dict (JSON.parsefile returns JSON.Object, but solve_scheduling_problem expects Dict)
    input_data = json_to_dict(json_obj)

    # Call the solver function from course_scheduler.jl
    # This will print the "Initial Heuristic Score" and Gurobi logs to stdout
    result = solve_scheduling_problem(input_data)
    
    # We don't strictly need to save the result JSON here as Python handles the logs,
    # but it's good practice to verify completion.
    println("✅ Batch Run Complete for $input_file")
end

main()