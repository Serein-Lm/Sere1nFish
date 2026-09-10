import {
  AudioOutlined,
  DatabaseOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  FileUnknownOutlined,
  FileWordOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import {
  getArtifactPresentation,
  type Artifact,
  type ArtifactFormatKey,
} from '../../../services/agentService'

const FORMAT_ICONS: Record<ArtifactFormatKey, React.ReactNode> = {
  word: <FileWordOutlined />,
  markdown: <FileMarkdownOutlined />,
  spreadsheet: <FileExcelOutlined />,
  pdf: <FilePdfOutlined />,
  data: <DatabaseOutlined />,
  image: <FileImageOutlined />,
  audio: <AudioOutlined />,
  video: <VideoCameraOutlined />,
  text: <FileTextOutlined />,
  file: <FileUnknownOutlined />,
}

export default function AIHubArtifactIcon({ artifact }: { artifact: Artifact }) {
  return FORMAT_ICONS[getArtifactPresentation(artifact).key]
}
