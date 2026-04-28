from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ConfigDict, Field

class ADOProject(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(pattern=r"^[a-zA-Z0-9_\-\. ]+$")

class ADORepository(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(pattern=r"^[a-zA-Z0-9_\-\. ]+$")
    name: str
    project: ADOProject

class ADOPullRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    pullRequestId: int
    status: str
    title: str
    description: Optional[str] = None
    repository: ADORepository
    lastMergeCommit: Optional[Dict[str, Any]] = None
    # Caso o utilizador ative "Work items to include" no Service Hook do ADO
    workItems: Optional[List[Dict[str, Any]]] = None

class ADOWebhookPayload(BaseModel):
    """Azure DevOps Pull Request Webhook Payload Model."""
    model_config = ConfigDict(extra="allow")
    
    eventType: str
    resource: ADOPullRequest
    resourceVersion: str
    message: Optional[Dict[str, Any]] = None
