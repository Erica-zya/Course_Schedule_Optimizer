using JuMP
using Gurobi
using JSON
using Dates
const MOI = JuMP.MOI

# =================================================================================
# Gurobi License Setup
# =================================================================================

function setup_gurobi_license()
    wls_access_id = get(ENV, "WLSACCESSID", "")
    wls_secret    = get(ENV, "WLSSECRET", "")
    license_id    = get(ENV, "LICENSEID", "")
    
    if !isempty(wls_access_id) && !isempty(wls_secret)
        println("🔑 Using Gurobi license from environment variables")
        ENV["GRB_WLSACCESSID"] = wls_access_id
        ENV["GRB_WLSSECRET"]   = wls_secret
        if !isempty(license_id)
            ENV["GRB_LICENSEID"] = license_id
        end
        return
    end
    
    license_file = joinpath(pwd(), "gurobi.lic")
    if isfile(license_file)
        println("🔑 Reading Gurobi license from $license_file")
        for line in eachline(license_file)
            line = strip(line)
            if !isempty(line) && !startswith(line, "#") && occursin("=", line)
                parts = split(line, "=", limit=2)
                if length(parts) == 2
                    key = strip(parts[1])
                    val = strip(parts[2])
                    ENV[key] = val
                    ENV["GRB_" * key] = val
                end
            end
        end
        println("✅ Gurobi license configured")
    else
        println("⚠️ No Gurobi license file found")
    end
end

# =================================================================================
# INPUT PARSING & HELPERS
# =================================================================================

function calculate_sessions(course, num_weeks, term_config)
    ctype      = get(course, "type", "full_term")
    period_min = term_config["period_length_minutes"]
    
    if ctype == "first_half_term"
        w_start = 1
        w_end   = div(num_weeks, 2)
        req_min = 3.0 * 60       
    elseif ctype == "second_half_term"
        w_start = div(num_weeks, 2) + 1
        w_end   = num_weeks
        req_min = 3.0 * 60
    else
        w_start = 1
        w_end   = num_weeks
        req_min = 1.5 * 60       
    end
    
    periods         = Int(ceil(req_min / period_min))
    active_weeks    = w_end - w_start + 1
    total_sessions  = active_weeks
    
    return (periods_per_session = periods,
            total_sessions      = total_sessions,
            week_start          = w_start,
            week_end            = w_end)
end

function get_lunch_periods(term_config, num_periods)
    day_start = term_config["day_start_time"]
    p_len     = term_config["period_length_minutes"]
    
    s_parts   = split(day_start, ":")
    start_min = parse(Int, s_parts[1]) * 60 + parse(Int, s_parts[2])
    
    lunch_start_min = 12 * 60
    lunch_end_min   = 12 * 60 + 30
    
    lunch_periods = Int[]
    for p in 1:num_periods
        p_start = start_min + (p-1)*p_len
        p_end   = p_start + p_len
        if max(p_start, lunch_start_min) < min(p_end, lunch_end_min)
            push!(lunch_periods, p)
        end
    end
    return lunch_periods
end

function parse_input(input_dict::Dict)
    term_config      = input_dict["term_config"]
    classrooms       = input_dict["classrooms"]
    instructors      = input_dict["instructors"]
    courses          = input_dict["courses"]
    students         = input_dict["students"]
    conflict_weights = input_dict["conflict_weights"]
    
    num_weeks = term_config["num_weeks"]
    days      = String[d for d in term_config["days"]]
    
    start_time     = term_config["day_start_time"]
    end_time       = term_config["day_end_time"]
    period_minutes = term_config["period_length_minutes"]
    
    s_parts = split(start_time, ":")
    e_parts = split(end_time,   ":")
    start_min = parse(Int, s_parts[1]) * 60 + parse(Int, s_parts[2])
    end_min   = parse(Int, e_parts[1]) * 60 + parse(Int, e_parts[2])
    day_mins  = end_min - start_min
    num_periods = div(day_mins, period_minutes)
    
    room_ids = String[r["id"] for r in classrooms]
    room_cap = Int[r["capacity"] for r in classrooms]
    
    inst_ids = String[i["id"] for i in instructors]
    
    # Availability
    avail = fill(true, length(instructors), length(days), num_periods)
    inst_b2b_pref = Int[get(i, "back_to_back_preference", 0) for i in instructors]
    
    for (i, inst) in enumerate(instructors)
        if haskey(inst, "availability") && !isempty(inst["availability"])
            avail[i, :, :] .= false
            for slot in inst["availability"]
                d_idx = findfirst(==(slot["day"]), days)
                p_idx = slot["period_index"] + 1
                if d_idx !== nothing && 1 <= p_idx <= num_periods
                    avail[i, d_idx, p_idx] = true
                end
            end
        end
    end
    
    inst_lunch_penalty = Float64[
        get(inst, "allow_lunch_teaching", true) ? 0.0 : 1.0
        for inst in instructors
    ]
    
    # Courses
    course_ids   = String[c["id"] for c in courses]
    course_inst  = Int[]
    course_enr   = Int[c["expected_enrollment"] for c in courses]
    for c in courses
        idx = findfirst(id -> id == c["instructor_id"], inst_ids)
        push!(course_inst, idx)
    end
    
    session_info        = [calculate_sessions(c, num_weeks, term_config) for c in courses]
    total_sessions      = [s.total_sessions for s in session_info]
    periods_per_session = [s.periods_per_session for s in session_info]
    week_starts         = [s.week_start for s in session_info]
    week_ends           = [s.week_end   for s in session_info]
    
    # Student conflicts
    students_cc = zeros(Int, length(courses), length(courses))
    c_id_map    = Dict(id => i for (i, id) in enumerate(course_ids))
    for s in students
        enrolled = [c_id_map[cid] for cid in s["enrolled_course_ids"] if haskey(c_id_map, cid)]
        for i in 1:length(enrolled), j in (i+1):length(enrolled)
            c1, c2 = enrolled[i], enrolled[j]
            students_cc[c1, c2] += 1
            students_cc[c2, c1] += 1
        end
    end
    
    w1 = Float64(get(conflict_weights, "global_student_conflict_weight",      1.0))
    w2 = Float64(get(conflict_weights, "instructor_compactness_weight",       1.0))
    w3 = Float64(get(conflict_weights, "preferred_time_slots_weight",         1.0))
    
    return (
        num_weeks          = num_weeks,
        days               = days,
        num_periods        = num_periods,
        room_ids           = room_ids,
        room_cap           = room_cap,
        inst_ids           = inst_ids,
        avail              = avail,
        inst_b2b_pref      = inst_b2b_pref,
        course_ids         = course_ids,
        course_inst        = course_inst,
        course_enr         = course_enr,
        total_sessions     = total_sessions,
        periods_per_session= periods_per_session,
        week_starts        = week_starts,
        week_ends          = week_ends,
        students_cc        = students_cc,
        w1                 = w1,
        w2                 = w2,
        w3                 = w3,
        inst_lunch_penalty = inst_lunch_penalty,
        courses            = courses,
        classrooms         = classrooms,
        instructors        = instructors,
        term_config        = term_config
    )
end

# =================================================================================
# GREEDY WARM START HEURISTIC
# =================================================================================

function create_greedy_warm_start(parsed, valid_x_set, course_blocks, C, D, P, R)
    println("🔥 Generating consistent greedy warm start...")
    warm_start = Dict{Tuple{Int,Int,Int,Int,Int}, Float64}()
    scheduled_rooms = Set{Tuple{Int,Int,Int,Int}}()
    scheduled_instructors = Set{Tuple{Int,Int,Int,Int}}()
    
    course_order = sort(1:C, by = c -> (length(course_blocks[c]) == 2 ? 0 : 1, -parsed.course_enr[c]))
    sessions_scheduled = 0
    
    for c in course_order
        inst = parsed.course_inst[c]
        dur  = parsed.periods_per_session[c]
        enr  = parsed.course_enr[c]
        blocks = course_blocks[c]
        
        total_weeks = parsed.week_ends[c] - parsed.week_starts[c] + 1
        sessions_per_week = Int(ceil(parsed.total_sessions[c] / total_weeks))
    
        assigned_count = 0
        for d in 1:D
            if assigned_count >= sessions_per_week; break; end
            for p in 1:(P - dur + 1)
                if assigned_count >= sessions_per_week; break; end
                
                candidate_rooms = Int[]
                for r in 1:R
                    if parsed.room_cap[r] >= enr
                        push!(candidate_rooms, r)
                    end
                end
                if isempty(candidate_rooms); continue; end
                
                final_room = -1
                for r in candidate_rooms
                    room_ok_all_blocks = true
                    inst_ok_all_blocks = true
                    
                    for b in blocks
                        if (c, b, d, p, r) ∉ valid_x_set
                            room_ok_all_blocks = false
                            break
                        end
                        for t in p:(p + dur - 1)
                            if (b, d, t, r) in scheduled_rooms
                                room_ok_all_blocks = false
                                break
                            end
                            if (inst, b, d, t) in scheduled_instructors
                                inst_ok_all_blocks = false
                                break
                            end
                        end
                        if !room_ok_all_blocks || !inst_ok_all_blocks; break; end
                    end
                    
                    if room_ok_all_blocks && inst_ok_all_blocks
                        final_room = r
                        break
                    end
                end
                
                if final_room != -1
                    for b in blocks
                         warm_start[(c, b, d, p, final_room)] = 1.0
                         for t in p:(p + dur - 1)
                             push!(scheduled_rooms, (b, d, t, final_room))
                             push!(scheduled_instructors, (inst, b, d, t))
                         end
                    end
                    assigned_count += 1
                end
            end
        end
        sessions_scheduled += assigned_count
    end
    println("   Greedy heuristic scheduled $sessions_scheduled sessions")
    return warm_start
end

# =================================================================================
# SCORE EVALUATOR (Computes Initial Penalty)
# =================================================================================

function calculate_heuristic_score(warm_start, parsed, course_blocks, block_weights, C, D, P)
    # Reconstruct simple schedules for evaluation
    # Schedule map: (c, b) -> [(d, p_start, p_end)]
    schedule = Dict{Tuple{Int,Int}, Vector{Tuple{Int,Int,Int}}}()
    
    for ((c, b, d, p, r), val) in warm_start
        if val > 0.5
            if !haskey(schedule, (c,b))
                schedule[(c,b)] = []
            end
            dur = parsed.periods_per_session[c]
            push!(schedule[(c,b)], (d, p, p + dur - 1))
        end
    end

    score_s1 = 0.0
    score_s2 = 0.0
    score_s3 = 0.0

    # S1: Student Conflicts (FIXED LOGIC: Multiply by overlapping periods)
    for c1 in 1:C, c2 in (c1+1):C
        if parsed.students_cc[c1, c2] > 0
            common_blocks = intersect(course_blocks[c1], course_blocks[c2])
            for b in common_blocks
                sched1 = get(schedule, (c1, b), [])
                sched2 = get(schedule, (c2, b), [])
                for (d1, s1, e1) in sched1, (d2, s2, e2) in sched2
                    if d1 == d2
                        # Overlap check
                        overlap_start = max(s1, s2)
                        overlap_end = min(e1, e2)
                        
                        if overlap_start <= overlap_end
                            # Calculate number of overlapping periods
                            overlap_len = overlap_end - overlap_start + 1
                            
                            # WEIGHTED BY BLOCK DURATION AND OVERLAP LENGTH
                            score_s1 += parsed.w1 * parsed.students_cc[c1, c2] * block_weights[b] * overlap_len
                        end
                    end
                end
            end
        end
    end

    # S2: Instructor Compactness (Using symmetric graph metric)
    for i in 1:length(parsed.inst_ids)
        pref = parsed.inst_b2b_pref[i]
        if pref == 0; continue; end
        
        my_courses = [c for c in 1:C if parsed.course_inst[c] == i]
        
        # Evaluate per block per day
        for b in 1:2
            for d in 1:D
                intervals = []
                for c in my_courses
                    if b in course_blocks[c]
                        sched = get(schedule, (c,b), [])
                        for (sd, s, e) in sched
                            if sd == d
                                push!(intervals, (s, e))
                            end
                        end
                    end
                end
                
                T = length(intervals)
                if T < 2; continue; end
                
                # Count back-to-back edges
                B = 0
                sort!(intervals)
                for k in 1:(length(intervals)-1)
                    if intervals[k][2] + 1 == intervals[k+1][1]
                        B += 1
                    end
                end
                
                # Symmetric metric: 2*B - (T-1)
                # WEIGHTED BY BLOCK DURATION
                score_s2 += parsed.w2 * pref * (2*B - (T - 1)) * block_weights[b]
            end
        end
    end

    # S3: Lunch (FIXED LOGIC: Multiply by number of lunch periods used)
    lunch_periods = get_lunch_periods(parsed.term_config, P)
    for ((c, b, d, p, r), val) in warm_start
        if val > 0.5
            dur = parsed.periods_per_session[c]
            s_end = p + dur - 1
            
            lunch_periods_used = 0
            for t in p:s_end
                if t in lunch_periods
                    lunch_periods_used += 1
                end
            end
            
            if lunch_periods_used > 0
                # WEIGHTED BY BLOCK DURATION AND NUMBER OF LUNCH PERIODS
                penalty = parsed.w3 * parsed.inst_lunch_penalty[parsed.course_inst[c]] * block_weights[b] * lunch_periods_used
                score_s3 += penalty
            end
        end
    end

    total = score_s1 + score_s2 + score_s3
    println("   📊 Initial Heuristic Score: $(total) (S1: $score_s1, S2: $score_s2, S3: $score_s3)")
    return total
end

# =================================================================================
# MODEL BUILDER
# =================================================================================

function build_and_solve_model(parsed)
    C = length(parsed.course_ids)
    I = length(parsed.inst_ids)
    R = length(parsed.room_ids)
    D = length(parsed.days)
    P = parsed.num_periods
    NUM_BLOCKS = 2 
    
    model = Model(Gurobi.Optimizer)
    
    # ========== GUROBI PARAMETERS ==========
    set_optimizer_attribute(model, "Threads", 8)
    set_optimizer_attribute(model, "OutputFlag", 1)
    set_optimizer_attribute(model, "TimeLimit", 1800.0)
    set_optimizer_attribute(model, "Presolve", 1)
    set_optimizer_attribute(model, "MIPFocus", 1) 
    
    println("🗓️ Building Model: $C courses, 2 Half-Terms, $D days, $P periods")

    # 1. Pre-compute Block Split & Weights
    # Calculate split point dynamically based on total weeks
    half_point = div(parsed.num_weeks, 2)
    # Weights: Number of weeks in each block
    # Block 1: Weeks 1 to half_point
    # Block 2: Weeks half_point+1 to num_weeks
    w1_len = half_point
    w2_len = parsed.num_weeks - half_point
    block_weights = [w1_len, w2_len]
    
    println("   Term Split: Block 1 = $w1_len weeks, Block 2 = $w2_len weeks")

    course_blocks = Vector{Vector{Int}}(undef, C)
    for c in 1:C
        w_start = parsed.week_starts[c]
        w_end   = parsed.week_ends[c]
        
        # Assign blocks based on week overlap
        if w_end <= half_point
            course_blocks[c] = [1]
        elseif w_start >= half_point + 1
            course_blocks[c] = [2]
        else
            course_blocks[c] = [1, 2]
        end
    end

    function is_inst_avail_in_block(inst_idx, block_idx, d, p, duration)
        for t in p:(p + duration - 1)
            if t > P return false end
            if !parsed.avail[inst_idx, d, t]
                return false
            end
        end
        return true
    end

    valid_x = Vector{Tuple{Int, Int, Int, Int, Int}}()
    for c in 1:C
        inst = parsed.course_inst[c]
        dur  = parsed.periods_per_session[c]
        enr  = parsed.course_enr[c]
        for b in course_blocks[c]
            for d in 1:D
                for p in 1:(P - dur + 1)
                    if !is_inst_avail_in_block(inst, b, d, p, dur)
                        continue
                    end
                    for r in 1:R
                        if parsed.room_cap[r] >= enr
                            push!(valid_x, (c, b, d, p, r))
                        end
                    end
                end
            end
        end
    end
    valid_x_set = Set(valid_x)

    # 2. Variables
    @variable(model, x[valid_x], Bin)
    @variable(model, h[1:I, 1:NUM_BLOCKS, 1:D, 1:P], Bin)

    valid_phi = Vector{Tuple{Int, Int, Int, Int, Int}}()
    for c1 in 1:C, c2 in (c1+1):C
        if parsed.students_cc[c1, c2] > 0
            common_blocks = intersect(course_blocks[c1], course_blocks[c2])
            for b in common_blocks, d in 1:D, p in 1:P
                push!(valid_phi, (c1, c2, b, d, p))
            end
        end
    end
    @variable(model, φ[valid_phi], Bin)
    @variable(model, π[1:C, 1:NUM_BLOCKS, 1:D, 1:P], Bin)
    @variable(model, has_teaching[1:I, 1:NUM_BLOCKS, 1:D], Bin)
    
    # 3. Warm Start
    warm_start_sol = create_greedy_warm_start(parsed, valid_x_set, course_blocks, C, D, P, R)
    initial_score = calculate_heuristic_score(warm_start_sol, parsed, course_blocks, block_weights, C, D, P)
    
    if !isempty(warm_start_sol)
        println("   Injecting greedy warm start...")
        for (key, val) in warm_start_sol
            if key in valid_x_set
                set_start_value(x[key], val)
            end
        end
    else
        set_optimizer_attribute(model, "NoRelHeurTime", 60)
    end

    # 4. Constraints
    function sum_x(c_iter, b_iter, d_iter, p_range, r_iter)
        expr = AffExpr(0.0)
        for c in c_iter, b in b_iter, d in d_iter, p in p_range, r in r_iter
            if (c,b,d,p,r) in valid_x_set
                 add_to_expression!(expr, x[(c,b,d,p,r)])
            end
        end
        return expr
    end

    # C1: Teacher Conflict
    for i in 1:I, b in 1:NUM_BLOCKS, d in 1:D, p in 1:P
        my_courses = [c for c in 1:C if parsed.course_inst[c] == i && b in course_blocks[c]]
        if !isempty(my_courses)
            occ = AffExpr(0.0)
            for c in my_courses
                dur = parsed.periods_per_session[c]
                s_start = max(1, p - dur + 1)
                s_end   = min(p, P - dur + 1)
                add_to_expression!(occ, sum_x(c, b, d, s_start:s_end, 1:R))
            end
            @constraint(model, occ <= 1)
            @constraint(model, h[i,b,d,p] == occ)
        else
            @constraint(model, h[i,b,d,p] == 0)
        end
    end

    # C2: Classroom Conflict
    for r in 1:R, b in 1:NUM_BLOCKS, d in 1:D, p in 1:P
        r_occ = AffExpr(0.0)
        for c in 1:C
            if b in course_blocks[c]
                 dur = parsed.periods_per_session[c]
                 s_start = max(1, p - dur + 1)
                 s_end   = min(p, P - dur + 1)
                 add_to_expression!(r_occ, sum_x(c, b, d, s_start:s_end, r))
            end
        end
        @constraint(model, r_occ <= 1)
    end

    # C3: Required Sessions
    for c in 1:C
        total_weeks = parsed.week_ends[c] - parsed.week_starts[c] + 1
        if total_weeks < 1; total_weeks = 1; end
        sessions_per_week = Int(ceil(parsed.total_sessions[c] / total_weeks))
        dur = parsed.periods_per_session[c]
        for b in course_blocks[c]
            @constraint(model, sum_x(c, b, 1:D, 1:(P-dur+1), 1:R) == sessions_per_week)
        end
    end

    # C8: One per day
    for c in 1:C, b in course_blocks[c], d in 1:D
        dur = parsed.periods_per_session[c]
        @constraint(model, sum_x(c, b, d, 1:(P-dur+1), 1:R) <= 1)
    end

    # Full Term Consistency
    for c in 1:C
        if course_blocks[c] == [1, 2]
             for d in 1:D, p in 1:(P - parsed.periods_per_session[c] + 1), r in 1:R
                if (c,1,d,p,r) in valid_x_set && (c,2,d,p,r) in valid_x_set
                     @constraint(model, x[(c,1,d,p,r)] == x[(c,2,d,p,r)])
                end
            end
         end
    end

    # Soft Constraints
    obj_s1 = AffExpr(0.0)
    for (c1, c2, b, d, p) in valid_phi
        dur1 = parsed.periods_per_session[c1]
        occ1 = sum_x(c1, b, d, max(1, p - dur1 + 1):min(p, P - dur1 + 1), 1:R)
        dur2 = parsed.periods_per_session[c2]
        occ2 = sum_x(c2, b, d, max(1, p - dur2 + 1):min(p, P - dur2 + 1), 1:R)
        @constraint(model, occ1 + occ2 <= 1 + φ[(c1,c2,b,d,p)])
        
        # WEIGHTED BY BLOCK DURATION
        weight = block_weights[b]
        if weight > 0
            add_to_expression!(obj_s1, parsed.w1 * parsed.students_cc[c1,c2] * φ[(c1,c2,b,d,p)] * weight)
        end
    end

    # S2: Back-to-Back (Corrected Logic & Weighted)
    obj_s2_terms = Any[]
    for i in 1:I
        pref = parsed.inst_b2b_pref[i]
        if pref == 0 continue end
        my_courses = [c for c in 1:C if parsed.course_inst[c] == i]
        
        for b in 1:NUM_BLOCKS
            # Check weight first
            weight = block_weights[b]
            if weight <= 0 
                # Constrain has_teaching to 0 if block has no weeks (edge case)
                for d in 1:D
                    @constraint(model, has_teaching[i,b,d] == 0)
                end
                continue 
            end

            active = [c for c in my_courses if b in course_blocks[c]]
            if isempty(active)
                for d in 1:D
                    @constraint(model, has_teaching[i,b,d] == 0)
                end
                continue 
            end
            
            max_T = length(active)

            for d in 1:D
                # T_expr: Number of distinct sessions
                T_expr = AffExpr(0.0)
                for c in active
                    dur = parsed.periods_per_session[c]
                    add_to_expression!(T_expr, sum_x(c, b, d, 1:(P-dur+1), 1:R))
                end
                
                # Indicator constraints
                @constraint(model, has_teaching[i,b,d] <= T_expr)
                if max_T > 0
                    @constraint(model, has_teaching[i,b,d] >= T_expr / max_T)
                end

                # B_expr: Number of back-to-back edges
                B_expr = AffExpr(0.0)
                if length(active) >= 2
                    for idx1 in 1:length(active), idx2 in (idx1+1):length(active)
                        c1, c2 = active[idx1], active[idx2]
                        len1 = parsed.periods_per_session[c1]
                        
                        # Forward
                        for p in 1:(P - len1)
                            term1 = sum_x(c1, b, d, p, 1:R)
                            term2 = sum_x(c2, b, d, p+len1, 1:R)
                            if !isempty(term1.terms) && !isempty(term2.terms)
                                z_local = @variable(model, binary=true)
                                @constraint(model, z_local <= term1)
                                @constraint(model, z_local <= term2)
                                @constraint(model, z_local >= term1 + term2 - 1)
                                add_to_expression!(B_expr, z_local)
                            end
                            
                            # Reverse
                            len2 = parsed.periods_per_session[c2]
                            if p + len2 <= P
                                term2r = sum_x(c2, b, d, p, 1:R)
                                term1r = sum_x(c1, b, d, p+len2, 1:R)
                                if !isempty(term2r.terms) && !isempty(term1r.terms)
                                    z_rev = @variable(model, binary=true)
                                    @constraint(model, z_rev <= term2r)
                                    @constraint(model, z_rev <= term1r)
                                    @constraint(model, z_rev >= term2r + term1r - 1)
                                    add_to_expression!(B_expr, z_rev)
                                end
                            end
                        end
                    end
                end
                
                # WEIGHTED BY BLOCK DURATION
                # logic: weight * pref * has_teaching * (2*edges - (nodes - 1)) * block_duration
                push!(obj_s2_terms, parsed.w2 * pref * has_teaching[i,b,d] * (2*B_expr - T_expr + 1) * weight)
            end
        end
    end
    obj_s2 = isempty(obj_s2_terms) ? 0.0 : sum(obj_s2_terms)

    # S3: Lunch (Weighted)
    lunch_periods = get_lunch_periods(parsed.term_config, P)
    obj_s3 = AffExpr(0.0)
    for c in 1:C, b in course_blocks[c], d in 1:D, p in lunch_periods
        dur = parsed.periods_per_session[c]
        occ = sum_x(c, b, d, max(1, p - dur + 1):min(p, P - dur + 1), 1:R)
        if !isempty(occ.terms)
            @constraint(model, occ <= π[c,b,d,p])
            
            # WEIGHTED BY BLOCK DURATION
            weight = block_weights[b]
            if weight > 0
                add_to_expression!(obj_s3, parsed.w3 * parsed.inst_lunch_penalty[parsed.course_inst[c]] * π[c,b,d,p] * weight)
            end
        end
    end

    @objective(model, Min, obj_s1 + obj_s2 + obj_s3)
    println("   🚀 Optimizing...")
    optimize!(model)

    x_dense = zeros(Float64, C, parsed.num_weeks, D, P, R)
    if has_values(model)
        for idx in valid_x
            if value(x[idx]) > 0.5
                (c, b, d, p, r) = idx
                
                # Reconstruct timeline based on dynamic split
                w_range = (b == 1) ? (1:half_point) : (half_point+1:parsed.num_weeks)
                
                for w in w_range
                    if w >= parsed.week_starts[c] && w <= parsed.week_ends[c]
                         x_dense[c, w, d, p, r] = 1.0
                    end
                end
            end
        end
    end

    return model, x_dense, obj_s1, obj_s2, obj_s3, initial_score
end

# =================================================================================
# OUTPUT FORMATTERS
# =================================================================================

function format_infeasible_solution(model, parsed)
    run_time = try solve_time(model) catch; 0.0 end
    return Dict(
        "status"           => "infeasible",
        "objective_value"    => nothing,
        "solve_time_seconds" => run_time,
        "hard_constraints_ok"=> false,
        "violated_hard_constraints" => ["multiple_constraints"],
        "soft_constraint_summary"   => Dict(),
        "schedule" => Dict("assignments" => []),
        "diagnostics" => Dict("infeasibility_explanation" => "No feasible schedule found"),
        "metadata" => Dict("solver" => "Gurobi (Julia/JuMP)")
    )
end

function format_optimal_solution(model, x, parsed, initial_score, obj_s1, obj_s2, obj_s3)
    if !has_values(model)
        return format_infeasible_solution(model, parsed)
    end
    
    assignments = []
    vals = value.(x)
    C,W,D,P,R = size(vals)
    session_counters = Dict{Int,Int}()
    for c in 1:C; session_counters[c] = 0; end
    
    for c in 1:C, w in 1:W, d in 1:D, p in 1:P, r in 1:R
        if vals[c,w,d,p,r] > 0.5
            session_counters[c] += 1
            snum = session_counters[c]
            course = parsed.courses[c]
            room   = parsed.classrooms[r]
            inst   = parsed.instructors[parsed.course_inst[c]]
            push!(assignments, Dict(
                "course_id"         => parsed.course_ids[c],
                "course_session_id" => "$(parsed.course_ids[c])_S$(snum)",
                "session_number"    => snum,
                "course_name"       => get(course, "name", ""),
                "room_id"           => parsed.room_ids[r],
                "room_name"         => get(room, "name", ""),
                "week"              => w - 1,
                "day"               => parsed.days[d],
                "period_start"      => p - 1,
                "period_length"     => parsed.periods_per_session[c],
                "instructor_id"     => get(inst, "id", ""),
                "instructor_name"   => get(inst, "name", "")
            ))
        end
    end
    
    obj_val = has_values(model) ? objective_value(model) : nothing
    s1_val = has_values(model) ? value(obj_s1) : 0.0
    s2_val = has_values(model) ? value(obj_s2) : 0.0
    s3_val = has_values(model) ? value(obj_s3) : 0.0
    
    improvement_str = if obj_val !== nothing && initial_score > 0
        reduction = initial_score - obj_val
        pct = (reduction / initial_score) * 100
        "The solver reduced the schedule penalties by ~$(round(Int, pct))% (from $(round(Int, initial_score)) to $(round(Int, obj_val)))"
    elseif obj_val !== nothing
        "Optimization complete. Final penalty: $(round(Int, obj_val))"
    else
        "Optimization complete."
    end

    println("   🏆 $improvement_str")

    return Dict(
        "status"             => termination_status(model) == MOI.OPTIMAL ? "optimal" : "time_limit_feasible",
        "objective_value"    => obj_val,
        "improvement_summary"=> improvement_str,
        "solve_time_seconds" => solve_time(model),
        "hard_constraints_ok"=> true,
        "violated_hard_constraints" => String[],
        "soft_constraint_summary" => Dict(
            "S1_student_conflicts" => Dict("weighted_penalty" => s1_val, "weight" => parsed.w1),
            "S2_instructor_compactness" => Dict("weighted_penalty" => s2_val, "weight" => parsed.w2),
            "S3_preferred_time_slots" => Dict("weighted_penalty" => s3_val, "weight" => parsed.w3)
        ),
        "schedule" => Dict("assignments" => assignments),
        "metadata" => Dict("solver" => "Gurobi (Julia/JuMP)", "num_assignments" => length(assignments))
    )
end

function format_output(model, x, parsed, initial_score, obj_s1, obj_s2, obj_s3)
    status = termination_status(model)
    
    if status in (MOI.INFEASIBLE, MOI.INFEASIBLE_OR_UNBOUNDED)
        return format_infeasible_solution(model, parsed)
    elseif status == MOI.OPTIMAL || (status == MOI.TIME_LIMIT && has_values(model))
        return format_optimal_solution(model, x, parsed, initial_score, obj_s1, obj_s2, obj_s3)
    else
        return Dict(
            "status"             => string(status),
            "objective_value"    => nothing,
            "solve_time_seconds" => solve_time(model),
            "hard_constraints_ok"=> false,
            "schedule" => Dict("assignments" => []),
            "diagnostics" => Dict("message" => "Solver terminated with status: $(string(status))")
        )
    end
end

# =================================================================================
# MAIN ENTRY POINT
# =================================================================================

function solve_scheduling_problem(input_dict::Dict)
    try
        println("📊 Julia solver started at $(now())")
        setup_gurobi_license()
        parsed = parse_input(input_dict)
        model, x, obj_s1, obj_s2, obj_s3, initial_score = build_and_solve_model(parsed)
        output = format_output(model, x, parsed, initial_score, obj_s1, obj_s2, obj_s3)
        println("✅ Julia solver completed: $(output["status"])")
        return output
    catch e
        msg = string(e)
        println("❌ Julia solver error: $msg")
        tb = try sprint(showerror, e, catch_backtrace()) catch; "Stack trace unavailable" end
        println("   Traceback: $tb")
        return Dict("status" => "error", "error" => msg)
    end
end

include("what_if_analysis.jl")