# Vibe Coding Agent - 工具定义
# Copyright (c) 2026 xingluosama

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容。可指定行范围只读取需要的片段以节省 token。Debug 时先用 search_in_files 定位问题，再用行范围精准读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始），可选。用于只读取代码片段，节省 token。"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（含），可选。配合 start_line 实现片段读取。"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖文件。覆盖前建议先调用 read_file 备份原内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容"
                    }
                },
                "required": ["path", "content"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "替换文件中的指定文本片段。old_str 必须精确匹配文件中唯一一处。若匹配多处则报错，需提供更多上下文以唯一确定。用于针对性修改，避免重写整个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "要被替换的原始文本片段，必须与文件中的内容精确匹配（含缩进和换行）"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "替换后的新文本片段"
                    }
                },
                "required": ["path", "old_str", "new_str"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容，用于了解项目结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，相对于工作区根目录。默认 '.' 表示根目录。"
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": "在文件中搜索匹配的文本模式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "要搜索的文本或正则表达式"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索范围，可以是文件路径或目录。默认 '.' 搜索整个项目。"
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除文件或目录。不可逆操作，执行前应请求用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件或目录路径"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exec_cmd",
            "description": "执行 shell 命令并返回输出。禁止执行 sudo、rm -rf / 等危险操作。对不确定的命令先加 --dry-run 预览。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                        "default": 30
                    }
                },
                "required": ["command"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "init_project",
            "description": "脚手架初始化新项目，自动创建目录结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "项目类型，如 python、web、node 等"
                    },
                    "name": {
                        "type": "string",
                        "description": "项目名称"
                    }
                },
                "required": ["type", "name"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "install_dependency",
            "description": "安装项目依赖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "包名，如 flask、requests"
                    },
                    "manager": {
                        "type": "string",
                        "description": "包管理器，如 pip、npm。默认自动检测。"
                    }
                },
                "required": ["package"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "提交所有变更到 Git 仓库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "提交信息，使用约定式提交格式，如 feat: add user auth"
                    }
                },
                "required": ["message"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提问或请求确认。当需要用户做出选择、澄清需求、或确认危险操作时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "向用户提出的问题。使用 Markdown 格式，用 ## 标题突出要点。"
                    }
                },
                "required": ["question"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": "标记任务完成。完成后会自动将任务摘要和代码路径记录到 .agent_history.json。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "任务完成总结，包括创建/修改了哪些文件、实现了什么功能"
                    },
                    "code_path": {
                        "type": "string",
                        "description": "本次任务涉及的主要代码路径或目录"
                    }
                },
                "required": ["summary"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息，适用于需要最新数据的场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "用系统默认程序打开文件。用户说「打开某个文件」时调用此工具。支持所有常见文件类型（图片、文档、网页等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    }
]
