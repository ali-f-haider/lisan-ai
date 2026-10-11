"""Offline source comparisons. No engine or speaker-vote module is imported."""
import ast
import copy
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def tree(file):
    return ast.parse((ROOT / file).read_text(encoding='utf-8-sig'))


def source_namespace(file, functions=(), constants=()):
    nodes = []
    for original in tree(file).body:
        node = copy.deepcopy(original)
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            nodes.append(node)
    import languages, lang_text, language_prompts
    space = {'json': json, 're': re, 'languages': languages, 'lang_text': lang_text, 'language_prompts': language_prompts}
    exec(compile(ast.Module(nodes, type_ignores=[]), file, 'exec'), space)
    return space


def _capture(file, name, cutoff_name, space):
    node = copy.deepcopy(next(n for n in tree(file).body if isinstance(n, ast.FunctionDef) and n.name == name))
    cutoff = next(i for i, n in enumerate(node.body) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == cutoff_name for t in n.targets))
    node.body = node.body[:cutoff] + [ast.Return(ast.Name('prompt', ast.Load()))]
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module([node], type_ignores=[])), 'offline_capture', 'exec'), space)
    return space[name]


def translation(segments, glossary=None, paces=None, gender=None):
    space = source_namespace('gemini_service.py', ('_glossary_block',), ('GENDER_RULES',))
    space.update(source_namespace('config.py', (), ('CANONICAL_EMOTIONS',)))
    # Run actual preparation and prompt expression, stop BEFORE any request.
    return _capture('gemini_service.py','translate_segments','payload',space)('offline',segments,'offline-no-network',glossary,paces,gender)


def emotion(scene):
    space = source_namespace('emotion_listen.py', ('_flat',))
    constants = source_namespace('config.py', (), ('CANONICAL_EMOTIONS',))['CANONICAL_EMOTIONS']
    space['_EMOTIONS'] = tuple(t for t in constants if t not in ('slowly','drawn out','rushed','very fast','hesitant','stammering'))
    return _capture('emotion_listen.py','_payload','parts',space)([],scene)


def gender_namespace():
    return source_namespace('gender_context.py', ('clean','address_cue','_role_speaker','speaker_table','build_context'),
                            ('GENDERS','CONTEXT_LINES','MAX_CONTEXT_CHARS','_FEMALE_CUE','_MALE_CUE','_HONORIFIC','_ROLE_OF','PEOPLE_PROMPT'))
