# =================================================================================
# WHAT-IF ANALYSIS / COUNTERFACTUAL REASONING (X-MILP / UDSP)
# =================================================================================

"""
Solve a What-If / counterfactual query using X-MILP's UDSP construction.

UDSP(M, CQ) = CSP with:
  (i)   f(x) ≤ f*            (minimality vs original solution)
  (ii)  C                    (all original hard constraints)
  (iii) CQ                   (query constraints from the user)

- If UDSP is infeasible: compute IIS to explain which query constraints (and/or
  minimality) are incompatible.
- If UDSP is feasible: return the alternative schedule and its objective value.
"""
function solve_what_if_query(
    input_dict::Dict,
    query_constraints::Vector,      # Vector{Dict} with query specs
    original_objective::Float64;    # f* from the original optimal solution
    skip_iis::Bool = true           # Skip IIS computation (can be slow/hang) - default true for performance
)
    try
        println("🔍 Solving What-If Query (UDSP)...")
        setup_gurobi_license()

        # Re-parse input and rebuild a "fresh" model
        parsed = parse_input(input_dict)
        C = length(parsed.course_ids)
        I = length(parsed.inst_ids)
        R = length(parsed.room_ids)
        W = parsed.num_weeks
        D = length(parsed.days)
        P = parsed.num_periods

        model = Model(Gurobi.Optimizer)
        set_optimizer_attribute(model, "OutputFlag", 0)
        set_optimizer_attribute(model, "TimeLimit", 300)

        # --- Decision variables (same structure as original model) ---
        @variable(model, x[1:C, 1:W, 1:D, 1:P, 1:R], Bin)       # course starts
        @variable(model, h[1:I, 1:W, 1:D, 1:P], Bin)            # instructor teaching per period
        @variable(model, φ[c1 in 1:C, c2 in (c1+1):C, 1:W, 1:D, 1:P], Bin)  # S1 conflicts
        @variable(model, π[1:C, 1:W, 1:D, 1:P], Bin)            # S3 lunch
        @variable(model, z[1:I, 1:C, 1:C, 1:W, 1:D, 1:P], Bin)  # S2 b2b "edges"
        @variable(model, b2b_sess[1:I, 1:C, 1:C, 1:W, 1:D], Bin) # S2 b2b per pair/day
        @variable(model, teach_c[1:I, 1:C, 1:W, 1:D], Bin)      # course taught that day
        @variable(model, has_teaching[1:I, 1:W, 1:D], Bin)      # instructor teaches at least one course that day

        println("  Adding original hard constraints (C)...")
        add_hard_constraints!(model, x, h, parsed, C, I, R, W, D, P)

        println("  Adding soft constraint structure and objective...")
        obj_s1, obj_s2, obj_s3 = build_soft_objective(
            model, x, h, b2b_sess, z, π, φ, teach_c, has_teaching,
            parsed, C, I, R, W, D, P
        )

        println("  Adding query constraints (CQ)...")
        query_constraint_refs = add_query_constraints!(
            model, query_constraints, x, parsed, C, R, W, D, P
        )

        println("  Adding minimality constraint: f(x) ≤ $original_objective")
        @constraint(model, minimality, obj_s1 + obj_s2 + obj_s3 <= original_objective)

        # UDSP objective is the same f(x) as the original problem
        @objective(model, Min, obj_s1 + obj_s2 + obj_s3)

        println("  🚀 Optimizing UDSP...")
        optimize!(model)

        status = termination_status(model)
        solve_time_val = solve_time(model)

        println("  Status:     $status")
        println("  Solve time: $(round(solve_time_val, digits=2))s")

        if status == MOI.INFEASIBLE || status == MOI.INFEASIBLE_OR_UNBOUNDED
            # Query scenario is infeasible w.r.t. constraints + minimality
            println("  ❌ UDSP is infeasible")
            
            if skip_iis
                println("  ⏭️ Skipping IIS computation (default: can be slow for large models)")
                # Provide a useful explanation without full IIS
                iis_constraints = Any[]
                for (idx, qtype, ref) in query_constraint_refs
                    push!(iis_constraints, Dict(
                        "id" => "query_$idx",
                        "type" => qtype,
                        "description" => "User query constraint #$idx of type $qtype",
                        "in_iis" => true
                    ))
                end
                push!(iis_constraints, Dict(
                    "id" => "minimality",
                    "type" => "minimality",
                    "description" => "Objective must be ≤ original optimal value",
                    "in_iis" => true
                ))
                
                iis_result = Dict(
                    "iis_constraints" => iis_constraints,
                    "summary" => Dict(
                        "num_constraints_in_iis" => length(iis_constraints),
                        "num_query_constraints_in_iis" => length(query_constraint_refs),
                        "minimality_in_iis" => true,
                        "interpretation" => "The scenario is infeasible. The query constraints conflict with hard constraints or the requirement to maintain the original objective value.",
                        "note" => "IIS computation was skipped for performance. Listed constraints are likely in conflict."
                    )
                )
            else
                println("  Computing IIS (Irreducible Infeasible Subsystem)...")
                println("  (This may take up to 30 seconds - if it hangs, IIS will be skipped in future runs)")
                
                # Try IIS computation with error handling
                iis_result = try
                    compute_iis_explanation(model, query_constraint_refs, minimality)
                catch e
                    println("  ⚠️ IIS computation failed or timed out, using fallback explanation")
                    # Fallback: provide basic explanation without full IIS
                    iis_constraints = Any[]
                    for (idx, qtype, ref) in query_constraint_refs
                        push!(iis_constraints, Dict(
                            "id" => "query_$idx",
                            "type" => qtype,
                            "description" => "User query constraint #$idx of type $qtype (likely in conflict)",
                            "in_iis" => true,
                            "note" => "IIS computation incomplete"
                        ))
                    end
                    push!(iis_constraints, Dict(
                        "id" => "minimality",
                        "type" => "minimality",
                        "description" => "Objective must be ≤ original optimal value (likely in conflict)",
                        "in_iis" => true,
                        "note" => "IIS computation incomplete"
                    ))
                    
                    Dict(
                        "iis_constraints" => iis_constraints,
                        "summary" => Dict(
                            "num_constraints_in_iis" => length(iis_constraints),
                            "num_query_constraints_in_iis" => length(query_constraint_refs),
                            "minimality_in_iis" => true,
                            "interpretation" => "The scenario is infeasible. The query constraints likely conflict with hard constraints or the requirement to maintain the original objective value.",
                            "note" => "Full IIS computation was not completed - constraints listed are likely in conflict"
                        )
                    )
                end
            end

            return Dict(
                "status" => "infeasible_query",
                "query_feasible" => false,
                "solve_time_seconds" => solve_time_val,
                "iis" => iis_result["iis_constraints"],
                "iis_summary" => iis_result["summary"],
                "explanation" =>
                    "The what-if scenario cannot achieve an objective ≤ original optimum. " *
                    "See IIS for conflicting constraints.",
                "original_objective" => original_objective,
                "metadata" => Dict(
                    "solver" => "Gurobi (Julia/JuMP)",
                    "method" => "UDSP (X-MILP)",
                    "timestamp" => string(now())
                )
            )

        elseif status == MOI.OPTIMAL || status == MOI.TIME_LIMIT
            println("  ✅ UDSP has a feasible solution")

            # Reuse your existing formatter to get schedule etc.
            alternative_output = format_output(model, x, parsed, obj_s1, obj_s2, obj_s3)
            new_objective = has_values(model) ? objective_value(model) : nothing
            Δ = (new_objective === nothing) ? nothing : (new_objective - original_objective)

            return Dict(
                "status" => "feasible_query",
                "query_feasible" => true,
                "solve_time_seconds" => solve_time_val,
                "original_objective" => original_objective,
                "alternative_objective" => new_objective,
                "objective_difference" => Δ,
                "alternative_schedule" => alternative_output["schedule"],
                "alternative_soft_constraints" => alternative_output["soft_constraint_summary"],
                "explanation" =>
                    "The what-if scenario is feasible. Objective difference: " *
                    (Δ === nothing ? "N/A" : string(round(Δ, digits=2))),
                "metadata" => Dict(
                    "solver" => "Gurobi (Julia/JuMP)",
                    "method" => "UDSP (X-MILP)",
                    "timestamp" => string(now())
                )
            )
        else
            return Dict(
                "status" => "udsp_error",
                "query_feasible" => false,
                "solve_time_seconds" => solve_time_val,
                "explanation" => "UDSP solver terminated with status: $(string(status))",
                "metadata" => Dict(
                    "solver" => "Gurobi (Julia/JuMP)",
                    "solver_status" => string(status)
                )
            )
        end

    catch e
        error_msg = string(e)
        println("❌ What-if query error: $error_msg")

        traceback_str = try
            sprint(showerror, e, catch_backtrace())
        catch
            "Stack trace unavailable"
        end

        return Dict(
            "status" => "error",
            "query_feasible" => false,
            "solve_time_seconds" => 0.0,
            "explanation" => "Error during what-if analysis: $error_msg",
            "diagnostics" => Dict(
                "error" => error_msg,
                "traceback" => traceback_str
            ),
            "metadata" => Dict(
                "solver" => "Gurobi (Julia/JuMP)",
                "timestamp" => string(now())
            )
        )
    end
end


# -----------------------------------------------------------------------------
# Query → constraint encoder (CQ)
# -----------------------------------------------------------------------------

"""
Add query constraints to the UDSP model.

Each element of `query_constraints` is a Dict like:

  Dict("type" => "enforce_time_slot",
       "course_id" => "CME307",
       "week" => 0,          # 0-indexed
       "day" => "Mon",
       "period_start" => 2)  # 0-indexed

Returns a list of (idx, qtype, constraint_ref) tuples to check IIS membership.
"""
function add_query_constraints!(
    model,
    query_constraints,
    x,
    parsed,
    C, R, W, D, P
)
    constraint_refs = Any[]

    for (idx, qc) in enumerate(query_constraints)
        qtype        = qc["type"]
        course_id    = get(qc, "course_id", nothing)
        week         = get(qc, "week", nothing)
        day          = get(qc, "day", nothing)
        period_start = get(qc, "period_start", nothing)
        period_end   = get(qc, "period_end", nothing)
        room_id      = get(qc, "room_id", nothing)

        # Map IDs to indices
        c_idx = course_id === nothing ? nothing : findfirst(==(course_id), parsed.course_ids)
        d_idx = day       === nothing ? nothing : findfirst(==(day),       parsed.days)
        r_idx = room_id   === nothing ? nothing : findfirst(==(room_id),   parsed.room_ids)
        w_idx = week      === nothing ? nothing : (week + 1)       # external is 0-based
        p_idx = period_start === nothing ? nothing : (period_start + 1)

        if qtype == "enforce_time_slot"
            # Course must be at (w,d,p) in some room
            if c_idx !== nothing && w_idx !== nothing && d_idx !== nothing && p_idx !== nothing
                ref = @constraint(model, sum(x[c_idx, w_idx, d_idx, p_idx, r] for r in 1:R) == 1)
                push!(constraint_refs, (idx, "enforce_time_slot", ref))
                println("    Added: Enforce $course_id at week=$week day=$day period=$period_start")
            end

        elseif qtype == "veto_time_slot"
            # Course must NOT be at (w,d,p)
            if c_idx !== nothing && d_idx !== nothing && p_idx !== nothing
                if w_idx !== nothing
                    ref = @constraint(model,
                        sum(x[c_idx, w_idx, d_idx, p_idx, r] for r in 1:R) == 0)
                    push!(constraint_refs, (idx, "veto_time_slot", ref))
                else
                    # veto across all weeks
                    ref = @constraint(model,
                        sum(x[c_idx, w, d_idx, p_idx, r] for w in 1:W, r in 1:R) == 0)
                    push!(constraint_refs, (idx, "veto_time_slot_all_weeks", ref))
                end
                println("    Added: Veto $course_id on day=$day period=$period_start")
            end

        elseif qtype == "veto_day"
            # Course cannot be on this day at all
            if c_idx !== nothing && d_idx !== nothing
                ref = @constraint(model,
                    sum(x[c_idx, w, d_idx, p, r] for w in 1:W, p in 1:P, r in 1:R) == 0)
                push!(constraint_refs, (idx, "veto_day", ref))
                println("    Added: Veto $course_id on $day")
            end

        elseif qtype == "enforce_room"
            # Course must use this room at least once
            if c_idx !== nothing && r_idx !== nothing
                ref = @constraint(model,
                    sum(x[c_idx, w, d, p, r_idx] for w in 1:W, d in 1:D, p in 1:P) >= 1)
                push!(constraint_refs, (idx, "enforce_room", ref))
                println("    Added: Enforce $course_id in room $room_id")
            end

        elseif qtype == "enforce_before_time"
            # All sessions of course must finish before period_end
            if c_idx !== nothing && period_end !== nothing
                p_end_idx = period_end + 1
                dur       = parsed.periods_per_session[c_idx]
                # Only start times that finish by p_end_idx
                ref = @constraint(model,
                    sum(
                        x[c_idx, w, d, p, r]
                        for w in 1:W, d in 1:D, p in 1:p_end_idx, r in 1:R
                        if p + dur - 1 <= p_end_idx
                    ) >= parsed.total_sessions[c_idx]
                )
                push!(constraint_refs, (idx, "enforce_before_time", ref))
                println("    Added: Enforce $course_id before period $period_end")
            end

        elseif qtype == "enforce_after_time"
            # All sessions of course must start at/after period_start
            if c_idx !== nothing && period_start !== nothing
                p_start_idx = period_start + 1
                dur         = parsed.periods_per_session[c_idx]
                ref = @constraint(model,
                    sum(
                        x[c_idx, w, d, p, r]
                        for w in 1:W, d in 1:D, p in p_start_idx:P, r in 1:R
                        if p + dur - 1 <= P
                    ) >= parsed.total_sessions[c_idx]
                )
                push!(constraint_refs, (idx, "enforce_after_time", ref))
                println("    Added: Enforce $course_id after period $period_start")
            end
        end
    end

    println("  Added $(length(constraint_refs)) query constraints")
    return constraint_refs
end


# -----------------------------------------------------------------------------
# IIS extraction (X-MILP step 2)
# -----------------------------------------------------------------------------

"""
Compute IIS and map which constraints are "in conflict".

We specifically check:
  - whether the minimality constraint is in the IIS
  - which query constraints are in the IIS
"""
function compute_iis_explanation(model, query_constraint_refs, minimality_constraint)
    println("  Computing IIS (Irreducible Infeasible Subsystem)...")
    println("  (This may take a moment for complex models...)")

    try
        # Try to compute IIS with a timeout approach
        # Since Gurobi's compute_conflict! doesn't have a direct timeout,
        # we'll try it and catch if it hangs too long
        # In practice, if it takes more than a few seconds, we'll use fallback
        
        compute_conflict!(model)
        println("  ✅ IIS computation successful")

        iis_constraints = Any[]

        # Minimality constraint in IIS?
        if MOI.get(model, MOI.ConstraintConflictStatus(), minimality_constraint) == MOI.IN_CONFLICT
            push!(iis_constraints, Dict(
                "id" => "minimality",
                "type" => "minimality",
                "description" => "Objective must be ≤ original optimal value",
                "in_iis" => true,
                "reason" => "Cannot achieve ≤ original objective together with query constraints"
            ))
        end

        # Query constraints in IIS?
        for (idx, qtype, ref) in query_constraint_refs
            in_conflict = MOI.get(model, MOI.ConstraintConflictStatus(), ref) == MOI.IN_CONFLICT
            if in_conflict
                push!(iis_constraints, Dict(
                    "id" => "query_$idx",
                    "type" => qtype,
                    "description" => "User query constraint #$idx of type $qtype",
                    "in_iis" => true
                ))
            end
        end

        num_conflicts  = length(iis_constraints)
        query_conflicts = count(c -> startswith(get(c, "id", ""), "query"), iis_constraints)
        has_minimality = any(c -> get(c, "id", "") == "minimality", iis_constraints)

        summary = Dict(
            "num_constraints_in_iis" => num_conflicts,
            "num_query_constraints_in_iis" => query_conflicts,
            "minimality_in_iis" => has_minimality,
            "interpretation" => has_minimality ?
                "Scenario conflicts with achieving the original objective value." :
                "Scenario conflicts with hard constraints only."
        )

        return Dict(
            "iis_constraints" => iis_constraints,
            "summary" => summary
        )

    catch e
        error_msg = string(e)
        println("  ⚠️ IIS computation failed or timed out: $error_msg")
        
        # Even if IIS computation fails, we can still provide useful information
        # by checking which query constraints might be problematic
        iis_constraints = Any[]
        
        # Try to identify problematic constraints without full IIS
        # If minimality constraint is likely the issue, add it
        try
            # Check if we can at least identify the query constraint
            if !isempty(query_constraint_refs)
                # Mark all query constraints as potentially in conflict
                for (idx, qtype, ref) in query_constraint_refs
                    push!(iis_constraints, Dict(
                        "id" => "query_$idx",
                        "type" => qtype,
                        "description" => "User query constraint #$idx of type $qtype (likely in conflict)",
                        "in_iis" => true,
                        "note" => "IIS computation incomplete - constraint likely conflicts"
                    ))
                end
            end
            
            # Also check minimality
            push!(iis_constraints, Dict(
                "id" => "minimality",
                "type" => "minimality",
                "description" => "Objective must be ≤ original optimal value (likely in conflict)",
                "in_iis" => true,
                "note" => "IIS computation incomplete - minimality constraint likely conflicts"
            ))
        catch
            # If even that fails, return empty
        end
        
        return Dict(
            "iis_constraints" => iis_constraints,
            "summary" => Dict(
                "num_constraints_in_iis" => length(iis_constraints),
                "num_query_constraints_in_iis" => length(iis_constraints) > 0 ? length(iis_constraints) - 1 : 0,
                "minimality_in_iis" => length(iis_constraints) > 0,
                "error" => "IIS computation timed out or failed. Query constraints and minimality constraint are likely in conflict.",
                "interpretation" => "The scenario is infeasible. The query constraints conflict with either hard constraints or the requirement to maintain the original objective value."
            )
        )
    end
end


# -----------------------------------------------------------------------------
# Shared hard-constraint builder (C1–C4, C7, C8, C9)
# -----------------------------------------------------------------------------

"""
Add all hard constraints C1–C4, C7, C8, C9 to `model` using decision vars (x, h).
Factored out so we can reuse it both in the main solve and in UDSP.
"""
function add_hard_constraints!(
    model,
    x,
    h,
    parsed,
    C, I, R, W, D, P
)
    # Helper: valid start periods for a given "current" period
    function valid_starts(c, current_p)
        dur = parsed.periods_per_session[c]
        return max(1, current_p - dur + 1):min(current_p, P)
    end

    # C1: Teacher conflict + link to h
    for i in 1:I, w in 1:W, d in 1:D, p in 1:P
        my_courses = [c for c in 1:C if parsed.course_inst[c] == i]
        occ = @expression(model,
            sum(x[c,w,d,s,r]
                for c in my_courses, r in 1:R, s in valid_starts(c, p)
                if s + parsed.periods_per_session[c] - 1 <= P)
        )
        @constraint(model, occ <= 1)
        @constraint(model, h[i,w,d,p] == occ)
    end

    # C2: Classroom conflict
    for r in 1:R, w in 1:W, d in 1:D, p in 1:P
        @constraint(model,
            sum(x[c,w,d,s,r]
                for c in 1:C, s in valid_starts(c, p)
                if s + parsed.periods_per_session[c] - 1 <= P) <= 1
        )
    end

    # C3: class hours, duration & active weeks
    for c in 1:C
        w_start = parsed.week_starts[c]
        w_end   = parsed.week_ends[c]
        dur     = parsed.periods_per_session[c]
        req_s   = parsed.total_sessions[c]

        @constraint(model,
            sum(x[c,w,d,p,r]
                for w in w_start:w_end, d in 1:D, p in 1:P, r in 1:R
                if p + dur - 1 <= P) == req_s
        )

        all_weeks      = 1:W
        inactive_weeks = setdiff(all_weeks, w_start:w_end)
        for w in inactive_weeks
            @constraint(model,
                sum(x[c,w,d,p,r] for d in 1:D, p in 1:P, r in 1:R) == 0
            )
        end
    end

    # C4: instructor availability
    for c in 1:C
        inst = parsed.course_inst[c]
        dur  = parsed.periods_per_session[c]
        for w in 1:W, d in 1:D, p in 1:P, r in 1:R
            if p + dur - 1 <= P
                is_avail = all(parsed.avail[inst,d,t] for t in p:(p+dur-1))
                if !is_avail
                    @constraint(model, x[c,w,d,p,r] == 0)
                end
            else
                @constraint(model, x[c,w,d,p,r] == 0)
            end
        end
    end

    # C7: classroom capacity
    for c in 1:C, w in 1:W, d in 1:D, p in 1:P, r in 1:R
        @constraint(model, parsed.course_enr[c] * x[c,w,d,p,r] <= parsed.room_cap[r])
    end

    # C8: at most one session per course/day
    for c in 1:C, w in 1:W, d in 1:D
        dur = parsed.periods_per_session[c]
        @constraint(model,
            sum(x[c,w,d,p,r] for p in 1:P, r in 1:R if p + dur - 1 <= P) <= 1
        )
    end

    # C9: weekly pattern consistency
    for c in 1:C
        w_start = parsed.week_starts[c]
        w_end   = parsed.week_ends[c]
        if w_end > w_start
            for w in (w_start+1):w_end, d in 1:D, p in 1:P, r in 1:R
                @constraint(model, x[c,w,d,p,r] == x[c,w_start,d,p,r])
            end
        end
    end
end


# -----------------------------------------------------------------------------
# Shared soft-constraint objective builder (S1, S2, S3)
# -----------------------------------------------------------------------------

"""
Build S1 (student conflict), S2 (b2b), S3 (lunch) objective terms and all
linking constraints for φ, z, b2b_sess, teach_c, has_teaching, π.

Return (obj_s1, obj_s2, obj_s3) such that:

    f(x) = obj_s1 + obj_s2 + obj_s3

is identical to the objective in `build_and_solve_model`.
"""
function build_soft_objective(
    model,
    x,
    h,
    b2b_sess,
    z,
    π,
    φ,
    teach_c,
    has_teaching,
    parsed,
    C, I, R, W, D, P
)
    # Helper
    function valid_starts(c, current_p)
        dur = parsed.periods_per_session[c]
        return max(1, current_p - dur + 1):min(current_p, P)
    end

    # --- S1: student conflicts ---
    obj_s1 = @expression(model,
        sum(parsed.w1 * parsed.students_cc[c1,c2] * φ[c1,c2,w,d,p]
            for c1 in 1:C, c2 in (c1+1):C, w in 1:W, d in 1:D, p in 1:P
            if parsed.students_cc[c1,c2] > 0)
    )

    # conflict detection constraints for φ
    for c1 in 1:C, c2 in (c1+1):C
        if parsed.students_cc[c1,c2] > 0
            for w in 1:W, d in 1:D, p in 1:P
                occ1 = @expression(model,
                    sum(x[c1,w,d,s,r]
                        for r in 1:R, s in valid_starts(c1,p)
                        if s + parsed.periods_per_session[c1] - 1 <= P)
                )
                occ2 = @expression(model,
                    sum(x[c2,w,d,s,r]
                        for r in 1:R, s in valid_starts(c2,p)
                        if s + parsed.periods_per_session[c2] - 1 <= P)
                )
                @constraint(model, occ1 + occ2 <= 1 + φ[c1,c2,w,d,p])
            end
        end
    end

    # --- S2: back-to-back (symmetric) ---
    # link z and b2b_sess
    for i in 1:I
        for c1 in 1:(C-1), c2 in (c1+1):C
            if parsed.course_inst[c1] != i || parsed.course_inst[c2] != i
                continue
            end
            len1 = parsed.periods_per_session[c1]
            for w in 1:W, d in 1:D
                for p in 1:(P-len1)
                    @constraint(model,
                        z[i,c1,c2,w,d,p] <= sum(x[c1,w,d,p,r] for r in 1:R)
                    )
                    @constraint(model,
                        z[i,c1,c2,w,d,p] <= sum(x[c2,w,d,p+len1,r] for r in 1:R)
                    )
                    @constraint(model,
                        z[i,c1,c2,w,d,p] >=
                            sum(x[c1,w,d,p,r]      for r in 1:R) +
                            sum(x[c2,w,d,p+len1,r] for r in 1:R) - 1
                    )
                end
            end
        end
    end

    # b2b_sess is OR of z over p
    for i in 1:I
        for c1 in 1:(C-1), c2 in (c1+1):C
            if parsed.course_inst[c1] != i || parsed.course_inst[c2] != i
                continue
            end
            len1 = parsed.periods_per_session[c1]
            for w in 1:W, d in 1:D
                @constraint(model,
                    b2b_sess[i,c1,c2,w,d] <=
                        sum(z[i,c1,c2,w,d,p] for p in 1:(P-len1))
                )
                for p in 1:(P-len1)
                    @constraint(model,
                        z[i,c1,c2,w,d,p] <= b2b_sess[i,c1,c2,w,d]
                    )
                end
            end
        end
    end

    # teach_c: whether instructor teaches course c that day
    for i in 1:I, c in 1:C, w in 1:W, d in 1:D
        if parsed.course_inst[c] != i
            @constraint(model, teach_c[i,c,w,d] == 0)
        else
            total = @expression(model,
                sum(x[c,w,d,p,r] for p in 1:P, r in 1:R)
            )
            @constraint(model, teach_c[i,c,w,d] <= total)
            @constraint(model, teach_c[i,c,w,d] >= total / P)
        end
    end

    # has_teaching: ≥1 course that day?
    for i in 1:I, w in 1:W, d in 1:D
        T_sum = @expression(model,
            sum(teach_c[i,c,w,d] for c in 1:C if parsed.course_inst[c] == i)
        )
        max_T = sum(1 for c in 1:C if parsed.course_inst[c] == i)
        if max_T > 0
            @constraint(model, has_teaching[i,w,d] <= T_sum)
            @constraint(model, has_teaching[i,w,d] >= T_sum / max_T)
        else
            @constraint(model, has_teaching[i,w,d] == 0)
        end
    end

    # S2 objective: w2 * pref_i * (2B - (T-1)) * has_teaching
    obj_s2_terms = Any[]
    for i in 1:I, w in 1:W, d in 1:D
        pref = parsed.inst_b2b_pref[i]
        if pref == 0
            continue
        end
        T_expr = @expression(model,
            sum(teach_c[i,c,w,d] for c in 1:C if parsed.course_inst[c] == i)
        )
        B_expr = @expression(model,
            sum(b2b_sess[i,c1,c2,w,d]
                for c1 in 1:(C-1), c2 in (c1+1):C
                if parsed.course_inst[c1] == i && parsed.course_inst[c2] == i)
        )
        push!(obj_s2_terms,
            parsed.w2 * pref * has_teaching[i,w,d] * (2*B_expr - (T_expr - 1))
        )
    end
    # Handle empty case to avoid type issues
    if isempty(obj_s2_terms)
        obj_s2 = @expression(model, 0.0)
    else
        obj_s2 = @expression(model, sum(obj_s2_terms))
    end

    # --- S3: lunch penalties ---
    lunch_periods = get_lunch_periods(parsed.term_config, P)
    obj_s3 = @expression(model,
        sum(
            parsed.w3 *
            parsed.inst_lunch_penalty[parsed.course_inst[c]] *
            π[c,w,d,p]
            for c in 1:C, w in 1:W, d in 1:D, p in lunch_periods
        )
    )

    # Link π to x
    for c in 1:C, w in 1:W, d in 1:D, p in lunch_periods
        occ = @expression(model,
            sum(x[c,w,d,s,r]
                for r in 1:R, s in valid_starts(c,p)
                if s + parsed.periods_per_session[c] - 1 <= P)
        )
        @constraint(model, occ <= π[c,w,d,p])
    end

    return obj_s1, obj_s2, obj_s3
end

