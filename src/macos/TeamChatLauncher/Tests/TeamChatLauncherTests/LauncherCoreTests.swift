import XCTest
@testable import TeamChatLauncher

final class LauncherCoreTests: XCTestCase {
    func testHubAddressValidation() {
        XCTAssertTrue(HubAddressValidator.isValid("127.0.0.1:8080"))
        XCTAssertTrue(HubAddressValidator.isValid("localhost:9000"))
        XCTAssertFalse(HubAddressValidator.isValid("localhost"))
        XCTAssertFalse(HubAddressValidator.isValid("localhost:99999"))
        XCTAssertFalse(HubAddressValidator.isValid("bad host:8080"))
    }

    func testPhase11AdminModelsDecodeSnakeCase() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let statusData = Data("""
        {
          "authenticated":true,
          "destroy_phrase":"DESTROY HUB",
          "local_hub_running":true,
          "admin_available":true,
          "admin_username":"admin",
          "denied_reason":"",
          "hub_runtime":{
            "hub_id":"hub_1",
            "pid":123,
            "hostname":"host",
            "machine_id":"machine",
            "host":"0.0.0.0",
            "port":8080,
            "temp_file_port":8081,
            "discovery_port":8090,
            "started_at":1,
            "updated_at":2,
            "status":"running"
          },
          "status":{
            "hub_dir":"/tmp/hub",
            "db_path":"/tmp/hub/hub.db",
            "device_count":2,
            "offline_queue_count":0,
            "event_count":1,
            "admin_initialized":true,
            "temp_file_dir":"/tmp/hub/temp_files"
          },
          "devices":[]
        }
        """.utf8)
        let status = try decoder.decode(HubAdminStatus.self, from: statusData)
        XCTAssertTrue(status.localHubRunning)
        XCTAssertEqual(status.adminUsername, "admin")
        XCTAssertEqual(status.hubRuntime?.port, 8080)
        XCTAssertTrue(status.status.adminInitialized)

        let authData = Data("""
        {"authenticated":true,"initialized":true,"admin_username":"admin","token":"secret"}
        """.utf8)
        let auth = try decoder.decode(HubAdminAuthResult.self, from: authData)
        XCTAssertEqual(auth.token, "secret")
    }

    func testPythonProcessBuilderUsesEnvForPathLookup() {
        let spec = PythonProcessBuilder.makeProcessSpec(
            pythonExecutable: "python3",
            pythonArguments: ["-m", "src.app.control"]
        )
        XCTAssertEqual(spec.executableURL.path, "/usr/bin/env")
        XCTAssertEqual(spec.arguments, ["python3", "-m", "src.app.control"])
    }

    func testPreferredPythonExecutableIsNotEmpty() {
        XCTAssertFalse(PythonInterpreterResolver.preferredExecutable().isEmpty)
    }

    func testPythonErrorCompactionKeepsUsefulTail() {
        let text = (1...20).map { "line \($0)" }.joined(separator: "\n")
        let compact = PythonInterpreterResolver.compactOutput(text, maxLines: 4, maxCharacters: 200)
        let lines = compact.split(whereSeparator: \.isNewline).map(String.init)
        XCTAssertFalse(lines.contains("line 1"))
        XCTAssertTrue(compact.contains("line 20"))
    }

    func testSidebarItemRawValueRoundTrip() {
        for item in SidebarItem.allCases {
            XCTAssertEqual(SidebarItem(rawValue: item.rawValue), item)
        }
        XCTAssertTrue(SidebarItem.allCases.contains(.install))
    }

    func testGlobalConfigDecodesPrePhase13Shape() throws {
        let data = Data("""
        {"projectRoot":"/tmp/project","pythonExecutable":"python3","selectedProfile":"alice"}
        """.utf8)
        let config = try JSONDecoder().decode(GlobalLauncherConfig.self, from: data)
        XCTAssertEqual(config.projectRoot, "/tmp/project")
        XCTAssertEqual(config.selectedProfile, "alice")
        XCTAssertEqual(config.installMode, "automatic")
        XCTAssertFalse(config.hasCompletedOnboarding)
        XCTAssertTrue(config.venvPath.isEmpty)
    }

    func testEnvironmentBootstrapEnvelopeDecodesSnakeCase() throws {
        let data = Data("""
        {
          "ok":true,
          "status":"needs_action",
          "venv_path":"/tmp/project/.venv",
          "python_executable":"/tmp/project/.venv/bin/python",
          "next_actions":["Install dependencies"],
          "copyable_commands":[{"title":"安装依赖","command":"python -m pip install -r requirements.txt"}],
          "logs":[{"level":"info","message":"Dry run","command":"","detail":"","exit_code":0}],
          "steps":[{"key":"python","title":"Python","status":"done","message":"3.11","repair_hint":""}]
        }
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let envelope = try decoder.decode(EnvironmentBootstrapEnvelope.self, from: data)
        let result = try envelope.asResult()
        XCTAssertEqual(result.status, "needs_action")
        XCTAssertEqual(result.venvPath, "/tmp/project/.venv")
        XCTAssertEqual(result.nextActions.first, "Install dependencies")
        XCTAssertEqual(result.copyableCommands.first?.command, "python -m pip install -r requirements.txt")
        XCTAssertEqual(result.steps.first?.label, "已完成")
    }

    func testChatClientArgumentsContainProfilePathsAndTicket() {
        let args = PythonProcessBuilder.chatClientArguments(
            profile: "alice",
            projectRoot: "/tmp/project",
            settings: ProfileLauncherSettings(transport: "network", hubAddress: "127.0.0.1:8080", logLevel: "INFO"),
            launchTicket: "ticket"
        )
        XCTAssertTrue(args.contains("src.app.main"))
        XCTAssertTrue(args.contains("alice"))
        XCTAssertTrue(args.contains("/tmp/project/runtime/profiles/alice/chat.db"))
        XCTAssertTrue(args.contains("ticket"))
    }

    func testLauncherEventDecodesSnakeCase() throws {
        let data = Data("""
        {"type":"login_ok","timestamp":1.0,"user_id":"u_1","display_name":"Alice","exit_code":0}
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let event = try decoder.decode(LauncherEvent.self, from: data)
        XCTAssertEqual(event.userId, "u_1")
        XCTAssertEqual(event.displayName, "Alice")
        XCTAssertEqual(event.exitCode, 0)
    }

    func testProjectFileSearchResultDecodesSnakeCaseAndExtension() throws {
        let data = Data("""
        {
          "id":"idxfile_1",
          "project_id":"prj_1",
          "group_id":"grp_1",
          "shared_folder_id":"sf_1",
          "project_name":"Project",
          "group_name":"Group",
          "root_path":"/tmp/project",
          "relative_path":"README.md",
          "absolute_path":"/tmp/project/README.md",
          "file_name":"README.md",
          "extension":"md",
          "size":12,
          "mtime":1.0,
          "mtime_ns":1000,
          "sha256":"abc",
          "mime_type":"text/markdown",
          "file_kind":"file",
          "exists":true,
          "hash_status":"hashed",
          "indexed_at":1.0,
          "updated_at":2.0
        }
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let result = try decoder.decode(ProjectFileSearchResult.self, from: data)
        XCTAssertEqual(result.fileName, "README.md")
        XCTAssertEqual(result.extension, "md")
        XCTAssertEqual(result.absolutePath, "/tmp/project/README.md")
    }

    func testSyncthingStatusDecodesPhase7RepairFields() throws {
        let data = Data("""
        {
          "state":"api_unconfigured",
          "base_url":"http://127.0.0.1:8384",
          "device_id":"",
          "error":"Syncthing GUI API Key 未配置或无效",
          "error_code":"api_key_or_csrf",
          "repair_hint":"配置 GUI API Key",
          "can_copy_device_id":false
        }
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(SyncthingStatus.self, from: data)
        XCTAssertEqual(status.errorCode, "api_key_or_csrf")
        XCTAssertEqual(status.repairHint, "配置 GUI API Key")
        XCTAssertEqual(status.canCopyDeviceId, false)
    }

    func testAISettingsAndSummaryDecodeSnakeCase() throws {
        let settingsData = Data("""
        {
          "provider_type":"ollama",
          "base_url":"http://127.0.0.1:11434",
          "model":"llama3",
          "api_key":"",
          "timeout_seconds":20,
          "max_file_bytes":204800,
          "max_document_bytes":1048576,
          "rag_max_context_chars":12000,
          "rag_max_chunks":8,
          "conversation_recent_turns":6,
          "embedding_enabled":false
        }
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let settings = try decoder.decode(AISettings.self, from: settingsData)
        XCTAssertEqual(settings.providerType, "ollama")
        XCTAssertEqual(settings.providerLabel, "Ollama")
        XCTAssertEqual(settings.maxDocumentBytes, 1048576)
        XCTAssertEqual(settings.ragMaxChunks, 8)

        let summaryData = Data("""
        {
          "summary":"OK",
          "context":{
            "group_id":"grp_1",
            "project_id":"prj_1",
            "file_count":2,
            "total_size":12,
            "extension_counts":{"md":1},
            "recent_files":[]
          }
        }
        """.utf8)
        let summary = try decoder.decode(AIProjectSummaryResult.self, from: summaryData)
        XCTAssertEqual(summary.context?.groupId, "grp_1")
        XCTAssertEqual(summary.context?.extensionCounts["md"], 1)
    }

    func testPhase9AIDocumentLibraryAndRAGDecodeSnakeCase() throws {
        let data = Data("""
        {
          "answer":"OK [S1]",
          "conversation_id":"aiconv_1",
          "user_message":{
            "message_id":"aimsg_u",
            "conversation_id":"aiconv_1",
            "role":"user",
            "content":"Question",
            "metadata":{},
            "created_at":1
          },
          "assistant_message":{
            "message_id":"aimsg_a",
            "conversation_id":"aiconv_1",
            "role":"assistant",
            "content":"OK [S1]",
            "metadata":{},
            "created_at":2
          },
          "sources":[
            {
              "source_index":"S1",
              "file_id":"idxfile_1",
              "source_id":"aisrc_1",
              "chunk_id":"aichunk_1",
              "relative_path":"README.md",
              "absolute_path":"/tmp/project/README.md",
              "line_start":1,
              "line_end":3,
              "snippet":"hello",
              "score":1.5,
              "sha256":"abc",
              "mtime_ns":1000,
              "size":12
            }
          ],
          "retrieval":{"mode":"fts_bm25","query":"Question","candidate_count":4,"source_count":1},
          "provider":{"provider_type":"ollama","base_url":"http://127.0.0.1:11434","model":"llama3","has_api_key":false},
          "privacy_policy":{
            "scope":"local_profile_project",
            "upload_policy":"only_retrieved_chunks_sent_to_selected_provider",
            "no_command_execution":true,
            "no_file_modification":true,
            "embedding_enabled":false
          }
        }
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let result = try decoder.decode(AIRAGAnswerResult.self, from: data)
        XCTAssertEqual(result.conversationId, "aiconv_1")
        XCTAssertEqual(result.sources.first?.sourceIndex, "S1")
        XCTAssertEqual(result.sources.first?.lineStart, 1)
        XCTAssertTrue(result.privacyPolicy.noCommandExecution)

        let statusData = Data("""
        {
          "group_id":"grp_1",
          "project_id":"prj_1",
          "candidate_count":2,
          "source_count":2,
          "indexed_source_count":1,
          "chunk_count":3,
          "pending_count":0,
          "stale_count":1,
          "missing_count":0,
          "skipped_count":1,
          "error_count":0,
          "total_size":42,
          "last_updated_at":2,
          "source_status_counts":{"indexed":1,"skipped":1},
          "embedding_status":"reserved_disabled",
          "tables_ready":true
        }
        """.utf8)
        let status = try decoder.decode(AILibraryStatus.self, from: statusData)
        XCTAssertEqual(status.chunkCount, 3)
        XCTAssertEqual(status.sourceStatusCounts["indexed"], 1)
    }

    func testPhase10DeletionModelsDecodeSnakeCase() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let sourceData = Data("""
        {
          "group_id":"grp_1",
          "project_id":"prj_1",
          "status":"",
          "query":"readme",
          "count":1,
          "real_files_deleted":false,
          "sources":[
            {
              "kind":"source",
              "source_id":"aisrc_1",
              "file_id":"idxfile_1",
              "project_id":"prj_1",
              "group_id":"grp_1",
              "relative_path":"README.md",
              "absolute_path":"/tmp/project/README.md",
              "file_name":"README.md",
              "extension":"md",
              "size":12,
              "sha256":"abc",
              "mtime_ns":1000,
              "mime_type":"text/markdown",
              "content_status":"deleted_local",
              "chunk_count":0,
              "last_error":"deleted locally",
              "indexed_at":1,
              "updated_at":2,
              "real_file_deleted":false
            }
          ]
        }
        """.utf8)
        let sources = try decoder.decode(AILibrarySourceListResult.self, from: sourceData)
        XCTAssertEqual(sources.sources.first?.sourceId, "aisrc_1")
        XCTAssertEqual(sources.sources.first?.statusLabel, "本机已删除")
        XCTAssertFalse(sources.realFilesDeleted)

        let unbindData = Data("""
        {
          "group_id":"grp_1",
          "project_id":"prj_1",
          "local_path":"/tmp/project",
          "local_path_exists":true,
          "previous_syncthing_folder_id":"",
          "syncthing_folder_removed":false,
          "local_only":true,
          "restart_required":false,
          "restart_check_error":"",
          "project_index":{
            "group_id":"grp_1",
            "project_id":"prj_1",
            "deleted_files":2,
            "deleted_runs":1,
            "real_files_deleted":false,
            "scope":"local_profile_project_index_only"
          },
          "ai_document_library":{
            "group_id":"grp_1",
            "project_id":"prj_1",
            "deleted_sources":1,
            "deleted_chunks":3,
            "deleted_fts":3,
            "deleted_embeddings":0,
            "real_files_deleted":false,
            "scope":"local_profile_ai_document_library_only"
          },
          "binding":{
            "group_id":"grp_1",
            "project_ids":["prj_1"],
            "shared_folder_ids":["sf_1"],
            "deleted_projects":1,
            "deleted_shared_folders":1,
            "deleted_sync_devices":0,
            "group_metadata_updated":true,
            "real_files_deleted":false,
            "messages_deleted":0,
            "file_attachments_deleted":0,
            "scope":"local_profile_project_sync_binding_only"
          },
          "real_files_deleted":false,
          "group_deleted":false,
          "messages_deleted":0,
          "scope":"local_profile_project_unbound"
        }
        """.utf8)
        let unbind = try decoder.decode(SyncUnbindResult.self, from: unbindData)
        XCTAssertEqual(unbind.projectIndex?.deletedFiles, 2)
        XCTAssertEqual(unbind.aiDocumentLibrary?.deletedSources, 1)
        XCTAssertEqual(unbind.binding?.deletedProjects, 1)
        XCTAssertFalse(unbind.realFilesDeleted)
        XCTAssertFalse(unbind.groupDeleted)
    }
}
