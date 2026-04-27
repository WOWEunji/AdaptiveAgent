---
description: "Use when handling self-correction scenarios in adaptive agents, taking error logs as input to guide precise code fixes based on probabilistic inference research."
tools: [read, edit, search]
argument-hint: "Error log: {error_log}"
---
# Self-Correction Prompt

Given an error log from a tool execution in an adaptive agent system, analyze the failure and suggest a corrected code snippet.

## Input
- Error log: The full error message and context from the failed execution.

## Process
1. Parse the error log to identify the type of failure (e.g., code generation error, environment issue).
2. Reference probabilistic inference scaling theory to assess if self-correction is reliable.
3. If reliable, provide the corrected code; otherwise, suggest human intervention.

## Output
Provide the corrected code block and an explanation of the fix.