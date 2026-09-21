import { useState } from 'react'
import { Check, Copy, Download } from 'lucide-react'

import { api } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Panel, ErrorBox } from '../components/Common'
import { Button } from '@/components/ui/button'

/** A small copy-to-clipboard button that confirms for a moment. */
function CopyButton({ text, label = '복사' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        } catch {
          //  Clipboard blocked (insecure origin, denied) - the text is on
          //  screen to copy by hand, so this is not worth surfacing.
        }
      }}
    >
      {done ? <Check /> : <Copy />}
      {done ? '복사됨' : label}
    </Button>
  )
}

function masterPrompt(endpoint: string, toolCount: number): string {
  return [
    '너는 셸 접근 권한이 있는 코딩 에이전트다. foldfront 단백질 설계 파이프라인을 설정하고 사용해라.',
    '',
    '1. 스킬 설치: 내려받은 protein-pipeline-stepper.zip 의 압축을 풀어(다운로드 폴더 확인)',
    '   protein-pipeline-stepper/ 폴더를 에이전트 skills 디렉터리에 넣는다',
    '   (Claude Code: ~/.claude/skills/). 그런 다음 클라이언트를 재시작한다.',
    '2. MCP 서버 등록: 이름 protein-pipeline',
    '   - 전송: HTTP (streamable)',
    `   - URL: ${endpoint}`,
    '   - 헤더: Authorization: Bearer <OIDC_ACCESS_TOKEN>',
    '3. 서버가 인식되지 않으면 재시작/새로고침하라고 나에게 말하고, 내가 확인하면 계속한다.',
    `4. tools/list 로 도구(현재 ${toolCount}종)를 확인한 뒤, protein-pipeline-stepper 스킬을 따라`,
    '   다음 분석을 수행한다: 〈여기에 목표를 적는다, 예: 이 FASTA 로 ColabFold 단독 실행〉',
  ].join('\n')
}

/** Connect an external coding agent to the pipeline over MCP. */
export function Mcp() {
  const info = useAsync(() => api.mcpInfo(), [])
  const endpoint = info.data?.endpoint ?? ''
  const toolCount = info.data?.tool_count ?? 0

  return (
    <>
      <PageHeader
        title="에이전트 연결"
        description="외부 코딩 에이전트를 MCP 로 파이프라인에 붙입니다. 스킬을 넣고 프롬프트 하나를 붙여넣으면 됩니다."
      />
      <ErrorBox message={info.error} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="1 · 스킬 내려받기" description="에이전트가 파이프라인을 단계별로 다루는 방법입니다.">
          <p className="text-muted-foreground mb-3 text-[13px]">
            압축을 풀어 <code className="font-mono">protein-pipeline-stepper/</code> 폴더를 에이전트의
            skills 디렉터리(Claude Code: <code className="font-mono">~/.claude/skills/</code>)에 넣고
            클라이언트를 재시작하면 인식됩니다.
          </p>
          <Button asChild variant="outline" size="sm">
            <a href={api.mcpSkillUrl()} download>
              <Download />
              스킬 내려받기 (.zip)
            </a>
          </Button>
        </Panel>

        <Panel title="2 · 엔드포인트와 인증" description={`도구 ${toolCount}종이 이 엔드포인트로 열립니다.`}>
          <dl className="flex flex-col gap-2.5 text-[13px]">
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground w-16 shrink-0">전송</dt>
              <dd className="font-mono">HTTP (streamable)</dd>
            </div>
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground w-16 shrink-0">URL</dt>
              <dd className="min-w-0 flex-1 truncate font-mono" title={endpoint}>
                {endpoint || '…'}
              </dd>
              {endpoint && <CopyButton text={endpoint} />}
            </div>
            <div className="flex items-start gap-2">
              <dt className="text-muted-foreground w-16 shrink-0">헤더</dt>
              <dd className="min-w-0 flex-1 font-mono">Authorization: Bearer &lt;OIDC 액세스 토큰&gt;</dd>
            </div>
          </dl>
          <p className="text-muted-foreground mt-3 text-[11.5px]">
            로그인 세션의 OIDC 액세스 토큰을 그대로 씁니다. 수명이 짧아 만료되면 다시 로그인해 갱신합니다.
            별도의 장수명 키는 발급하지 않습니다. (개발 환경은 인증이 꺼져 있습니다.)
          </p>
        </Panel>
      </div>

      <Panel
        title="3 · 마스터 프롬프트"
        description="에이전트에 그대로 붙여넣으면 나머지를 알아서 설정합니다."
        actions={<CopyButton text={masterPrompt(endpoint, toolCount)} label="프롬프트 복사" />}
      >
        <pre className="bg-muted overflow-x-auto rounded-md p-3 font-mono text-[12px] whitespace-pre-wrap">
          {masterPrompt(endpoint, toolCount)}
        </pre>
      </Panel>
    </>
  )
}
