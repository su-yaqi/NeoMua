# 项目概述

## 项目名称
NeoMua

## 背景
项目基于 Full Stack FastAPI Template 演进，保留模板自带的认证、用户、条目管理能力，并新增“空间（namespace）”这一多租户维度，逐步向平台化后台演进。

## 目标

- 提供一个前后端分离、可容器化部署的管理后台基础盘。
- 支持账号认证、个人设置、平台用户管理和基础业务条目管理。
- 支持按空间组织用户，并为后续多租户业务扩展预留统一入口。

## 技术栈
| 层次 | 技术选型 |
|------|--------|
| 后端 | Python 3.10+, FastAPI, SQLModel, Pydantic v2 |
| 前端 | React 19, TypeScript, Vite, TanStack Router, TanStack Query |
| 数据库 | PostgreSQL 18 |
| UI | Tailwind CSS 4, shadcn/ui, Radix UI |
| 测试 | Pytest, Playwright |
| 部署 | Docker Compose, Traefik, Adminer |

## 核心模块
| 模块 | 描述 |
|------|------|
| auth | 登录、注册、Token 校验、找回密码与重置密码 |
| users | 个人资料维护、密码修改、自助注销、平台级用户管理 |
| items | 用户个人条目的增删改查 |
| namespaces | 空间列表、空间 CRUD、空间成员关系与空间上下文选择 |
| llm_configs | 按空间维护多供应商大模型接入配置、连接校验与模型清单同步 |
| runtime_management | 空间级平台/节点运行时、Agent 任务、完整事件审计与签名内容分发 |

## 当前实现状态

- 后端已经实现 `users`、`items`、`namespaces`、`platform`、`llm` 五组主要 API。
- 前端已实现登录/注册/找回密码/重置密码、首页、条目页、用户管理页、个人设置页、空间管理页、大模型接入配置页。
- 前端已接入空间选择器和自定义 `tenantApi`，会自动携带 `X-Namespace-Id`。
- 空间成员管理已形成可用页面流；大模型接入配置支持供应商目录、配置新增/编辑、连接校验、模型同步与手工模型维护。
- v0.4 已实现独立 `runtime-worker`、`model-gateway` 与可安装 `node_runtime`，支持真实 Agent 执行、模型转发、节点 WSS 心跳和离线恢复。
- PostgreSQL 保存用户消息、Agent 消息、工具调用/结果、状态、错误和最终结果；节点使用 SQLite spool 保证断线重放。
- Agent/Skill/MCP/CLI/工作区内容以不可变 ZIP、双签名清单和短期下载令牌分发，节点独立校验并原子应用。

## 版本状态
| 版本 | 状态 | 说明 |
|------|------|------|
| v0.1 | 已完成 | 模板基础能力 + 命名空间模型、空间管理 API 与空间选择器接入 |
| v0.2 | 已完成 | 空间成员管理页、平台用户空间分配收敛与测试链路修正 |
| v0.3 | 已完成 | 空间级多供应商大模型接入配置、连接校验与模型同步能力 |
| v0.4 | 已完成 | Agent Runtime、节点守护进程、可靠任务执行与签名内容分发 |
