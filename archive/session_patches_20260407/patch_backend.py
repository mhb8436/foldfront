import re
with open('pipeline-mcp/src/pipeline_mcp/pipeline.py', 'r') as f:
    code = f.read()

target = """def _rfd3_simple_inputs(
    request: PipelineRequest, *, input_files: dict[str, str]
) -> dict[str, object]:"""

insert = """def _rfd3_simple_inputs(
    request: PipelineRequest, *, input_files: dict[str, str]
) -> dict[str, object]:
    from .bio.pdb import residues_by_chain
"""

code = code.replace(target, insert)

target2 = """    elif mode == "enzyme":
        if request.rfd3_contig is not None:
            spec["contig"] = _normalize_rfd3_contig_value(request.rfd3_contig)
        if request.rfd3_unindex is None:
            raise ValueError("RFD3 enzyme mode requires rfd3_unindex")
        spec["unindex"] = request.rfd3_unindex
        if request.rfd3_length is not None:
            spec["length"] = request.rfd3_length
        if request.rfd3_select_fixed_atoms is not None:
            spec["select_fixed_atoms"] = request.rfd3_select_fixed_atoms
    elif mode == "local_diversify":
        if request.rfd3_contig is not None:
            spec["contig"] = _normalize_rfd3_contig_value(request.rfd3_contig)
        if request.rfd3_unindex is not None:
            spec["unindex"] = request.rfd3_unindex
        if request.rfd3_select_fixed_atoms is not None:
            spec["select_fixed_atoms"] = request.rfd3_select_fixed_atoms"""

insert2 = """    elif mode in {"enzyme", "local_diversify"}:
        contig_val = request.rfd3_contig
        unindex_val = request.rfd3_unindex
        fixed_val = request.rfd3_select_fixed_atoms
        
        if unindex_val is None:
            pdb_text = request.rfd3_input_pdb or ""
            if not pdb_text.strip() and "input.pdb" in input_files:
                try:
                    with open(input_files["input.pdb"], "r") as f:
                        pdb_text = f.read()
                except Exception:
                    pass
            if pdb_text.strip():
                by_chain = residues_by_chain(pdb_text)
                if by_chain:
                    first_chain = sorted(by_chain.keys())[0]
                    res_list = by_chain[first_chain]
                    if len(res_list) > 1:
                        unindex_val = f"{first_chain}{res_list[0].resseq}"
                        fixed_val = {unindex_val: "ALL"}
                        if contig_val is None:
                            contig_val = f"{first_chain}{res_list[1].resseq}-{res_list[-1].resseq}"
        
        if mode == "enzyme" and unindex_val is None:
            raise ValueError("RFD3 enzyme mode requires rfd3_unindex")
            
        if contig_val is not None:
            spec["contig"] = _normalize_rfd3_contig_value(contig_val)
        if unindex_val is not None:
            spec["unindex"] = unindex_val
        if fixed_val is not None:
            spec["select_fixed_atoms"] = fixed_val
            
        if mode == "enzyme" and request.rfd3_length is not None:
            spec["length"] = request.rfd3_length"""

code = code.replace(target2, insert2)

with open('pipeline-mcp/src/pipeline_mcp/pipeline.py', 'w') as f:
    f.write(code)
