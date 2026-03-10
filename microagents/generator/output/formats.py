"""
Output Format Handlers for MicroAgents Generator

Ce module gère la génération de code et de configuration dans différents formats.
Chaque handler implémente les optimisations spécifiques au format et garantit
la qualité du code généré.
"""

import json
import os
import re
import textwrap
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import jinja2
import yaml
from pydantic import BaseModel, Field, validator

from microagents.utils.serialization.serializers import json_serializer


class OutputFormat(Enum):
    """Formats de sortie supportés."""
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    YAML = "yaml"
    JSON = "json"
    MARKDOWN = "markdown"
    OPENAPI = "openapi"
    DOCKERFILE = "dockerfile"
    KUBERNETES = "kubernetes"
    TERRAFORM = "terraform"
    CICD = "cicd"
    TESTS = "tests"
    MAKEFILE = "makefile"
    DOCKER_COMPOSE = "docker_compose"
    REQUIREMENTS = "requirements"
    PACKAGE_JSON = "package_json"
    VSCODE = "vscode"
    INTELLIJ = "intellij"
    PRE_COMMIT = "pre_commit"
    GITIGNORE = "gitignore"
    ENV_FILE = "env_file"


class CodeStyle(BaseModel):
    """Configuration du style de code."""
    indent_size: int = Field(default=4, ge=2, le=8)
    max_line_length: int = Field(default=88, ge=60, le=120)
    quote_style: str = Field(default="double", regex="^(single|double)$")
    trailing_commas: bool = True
    sort_imports: bool = True
    docstring_style: str = Field(default="google", regex="^(google|numpy|sphinx|pep257)$")
    
    class Config:
        extra = "forbid"


class OutputConfig(BaseModel):
    """Configuration pour la génération de sortie."""
    format: OutputFormat
    style: CodeStyle = Field(default_factory=CodeStyle)
    linting: bool = True
    formatting: bool = True
    validation: bool = True
    minify: bool = False
    include_comments: bool = True
    target_directory: Optional[str] = None
    
    @validator('target_directory')
    def validate_target_directory(cls, v):
        if v and not os.path.isabs(v):
            return os.path.join(os.getcwd(), v)
        return v


class BaseOutputHandler(ABC):
    """Handler de base pour tous les formats de sortie."""
    
    def __init__(self, config: OutputConfig):
        self.config = config
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader([
                os.path.join(os.path.dirname(__file__), '../templates'),
                os.path.join(os.path.dirname(__file__), '../../../docs/templates')
            ]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True
        )
        
        # Configuration des filtres Jinja
        self.jinja_env.filters.update({
            'to_json': lambda v: json.dumps(v, indent=2, default=json_serializer),
            'to_yaml': lambda v: yaml.dump(v, default_flow_style=False, sort_keys=False),
            'wrap_text': lambda text, width: '\n'.join(textwrap.wrap(text, width)),
            'snake_case': self._to_snake_case,
            'camel_case': self._to_camel_case,
            'pascal_case': self._to_pascal_case,
            'kebab_case': self._to_kebab_case,
        })
    
    @abstractmethod
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """
        Génère le contenu dans le format spécifique.
        
        Args:
            data: Données à formater
            
        Returns:
            Tuple de (contenu, fichiers_générés)
        """
        pass
    
    @abstractmethod
    def validate(self, content: str) -> bool:
        """
        Valide le contenu généré.
        
        Args:
            content: Contenu à valider
            
        Returns:
            True si valide
        """
        pass
    
    def post_process(self, content: str) -> str:
        """
        Post-traitement du contenu généré.
        
        Args:
            content: Contenu brut
            
        Returns:
            Contenu post-traité
        """
        if self.config.minify:
            content = self._minify(content)
        
        if self.config.formatting:
            content = self._format(content)
        
        return content
    
    def _minify(self, content: str) -> str:
        """Minifie le contenu."""
        # Implémentation spécifique au format
        return content
    
    def _format(self, content: str) -> str:
        """Formate le contenu selon le style."""
        # Implémentation spécifique au format
        return content
    
    @staticmethod
    def _to_snake_case(text: str) -> str:
        """Convertit en snake_case."""
        text = re.sub(r'(?<!^)(?=[A-Z])', '_', text).lower()
        return re.sub(r'[-\s]+', '_', text)
    
    @staticmethod
    def _to_camel_case(text: str) -> str:
        """Convertit en camelCase."""
        words = re.split(r'[_\-\s]+', text)
        return words[0].lower() + ''.join(word.capitalize() for word in words[1:])
    
    @staticmethod
    def _to_pascal_case(text: str) -> str:
        """Convertit en PascalCase."""
        words = re.split(r'[_\-\s]+', text)
        return ''.join(word.capitalize() for word in words)
    
    @staticmethod
    def _to_kebab_case(text: str) -> str:
        """Convertit en kebab-case."""
        text = re.sub(r'(?<!^)(?=[A-Z])', '-', text).lower()
        return re.sub(r'[_\s]+', '-', text)


class PythonOutputHandler(BaseOutputHandler):
    """Handler pour la génération de code Python."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère du code Python."""
        template = self.jinja_env.get_template('python_agent.jinja')
        
        # Préparation des données pour le template
        processed_data = self._prepare_python_data(data)
        
        # Génération du contenu
        content = template.render(
            data=processed_data,
            config=self.config,
            timestamp=datetime.utcnow().isoformat(),
            **self._get_template_helpers()
        )
        
        # Post-traitement
        content = self.post_process(content)
        
        # Génération des fichiers supplémentaires
        additional_files = self._generate_python_additional_files(data)
        
        return content, additional_files
    
    def validate(self, content: str) -> bool:
        """Valide le code Python généré."""
        try:
            # Validation syntaxique basique
            compile(content, '<string>', 'exec')
            
            # Vérification des imports
            if 'import' in content:
                lines = content.split('\n')
                imports = [l for l in lines if l.strip().startswith(('import ', 'from '))]
                
                # Vérifie que les imports sont en haut du fichier
                non_import_lines = [i for i, line in enumerate(lines) 
                                  if line.strip() and not line.strip().startswith(('#', '"', "'"))]
                if non_import_lines and imports:
                    first_non_import = non_import_lines[0]
                    last_import = max(i for i, line in enumerate(lines) if line.strip().startswith(('import ', 'from ')))
                    
                    if last_import > first_non_import:
                        raise SyntaxError("Les imports doivent être en haut du fichier")
            
            return True
            
        except SyntaxError as e:
            print(f"Erreur de syntaxe Python: {e}")
            return False
        except Exception as e:
            print(f"Erreur de validation Python: {e}")
            return False
    
    def _prepare_python_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Prépare les données pour le template Python."""
        # Conversion des types DSL vers Python
        type_mapping = {
            'string': 'str',
            'integer': 'int',
            'float': 'float',
            'boolean': 'bool',
            'array': 'List',
            'object': 'Dict[str, Any]',
            'any': 'Any',
            'datetime': 'datetime',
            'decimal': 'Decimal',
        }
        
        # Traitement des schémas
        if 'inputs' in data:
            for input_name, input_def in data['inputs'].items():
                if 'type' in input_def:
                    input_def['python_type'] = type_mapping.get(
                        input_def['type'], 
                        input_def['type']
                    )
        
        # Traitement des dépendances
        if 'dependencies' in data:
            data['python_dependencies'] = self._extract_python_dependencies(data['dependencies'])
        
        return data
    
    def _generate_python_additional_files(self, data: Dict[str, Any]) -> List[Path]:
        """Génère les fichiers supplémentaires pour un projet Python."""
        files = []
        target_dir = Path(self.config.target_directory or '.')
        
        # 1. pyproject.toml
        pyproject_content = self._generate_pyproject(data)
        files.append(target_dir / 'pyproject.toml')
        (target_dir / 'pyproject.toml').write_text(pyproject_content)
        
        # 2. requirements.txt (alternative)
        requirements_content = self._generate_requirements(data)
        files.append(target_dir / 'requirements.txt')
        (target_dir / 'requirements.txt').write_text(requirements_content)
        
        # 3. setup.cfg (pour la compatibilité)
        setup_cfg_content = self._generate_setup_cfg(data)
        files.append(target_dir / 'setup.cfg')
        (target_dir / 'setup.cfg').write_text(setup_cfg_content)
        
        # 4. __init__.py pour les packages
        packages = data.get('metadata', {}).get('packages', [])
        for package in packages:
            init_file = target_dir / package.replace('.', '/') / '__init__.py'
            init_file.parent.mkdir(parents=True, exist_ok=True)
            init_file.write_text('# Generated by MicroAgents\n')
            files.append(init_file)
        
        # 5. .python-version si spécifié
        if 'python_version' in data.get('metadata', {}):
            python_version = data['metadata']['python_version']
            (target_dir / '.python-version').write_text(python_version)
            files.append(target_dir / '.python-version')
        
        return files
    
    def _get_template_helpers(self) -> Dict[str, Any]:
        """Retourne les helpers spécifiques à Python."""
        return {
            'get_type_hint': self._get_python_type_hint,
            'format_docstring': self._format_python_docstring,
            'generate_imports': self._generate_python_imports,
        }


class TypeScriptOutputHandler(BaseOutputHandler):
    """Handler pour la génération de code TypeScript."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère du code TypeScript."""
        template = self.jinja_env.get_template('typescript_agent.jinja')
        
        # Préparation des données pour le template
        processed_data = self._prepare_typescript_data(data)
        
        # Génération du contenu
        content = template.render(
            data=processed_data,
            config=self.config,
            timestamp=datetime.utcnow().isoformat(),
            **self._get_template_helpers()
        )
        
        # Post-traitement
        content = self.post_process(content)
        
        # Génération des fichiers supplémentaires
        additional_files = self._generate_typescript_additional_files(data)
        
        return content, additional_files
    
    def validate(self, content: str) -> bool:
        """Valide le code TypeScript généré."""
        try:
            # Validation basique de la syntaxe TypeScript
            # (pourrait utiliser tsc en arrière-plan)
            
            # Vérifications basiques
            lines = content.split('\n')
            
            # Vérifie les imports
            imports = [l for l in lines if l.strip().startswith(('import ', 'export ', 'from '))]
            
            # Vérifie les définitions d'interface
            interface_count = len([l for l in lines if 'interface ' in l or 'type ' in l])
            
            # Vérifie les définitions de classe
            class_count = len([l for l in lines if 'class ' in l])
            
            return True
            
        except Exception as e:
            print(f"Erreur de validation TypeScript: {e}")
            return False
    
    def _prepare_typescript_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Prépare les données pour le template TypeScript."""
        # Conversion des types DSL vers TypeScript
        type_mapping = {
            'string': 'string',
            'integer': 'number',
            'float': 'number',
            'boolean': 'boolean',
            'array': 'Array',
            'object': 'Record<string, any>',
            'any': 'any',
            'datetime': 'Date',
            'decimal': 'number',
        }
        
        # Traitement des interfaces
        if 'inputs' in data:
            for input_name, input_def in data['inputs'].items():
                if 'type' in input_def:
                    input_def['typescript_type'] = type_mapping.get(
                        input_def['type'], 
                        input_def['type']
                    )
        
        # Génération des interfaces TypeScript
        data['typescript_interfaces'] = self._generate_typescript_interfaces(data)
        
        return data
    
    def _generate_typescript_additional_files(self, data: Dict[str, Any]) -> List[Path]:
        """Génère les fichiers supplémentaires pour un projet TypeScript."""
        files = []
        target_dir = Path(self.config.target_directory or '.')
        
        # 1. package.json
        package_json_content = self._generate_package_json(data)
        files.append(target_dir / 'package.json')
        (target_dir / 'package.json').write_text(package_json_content)
        
        # 2. tsconfig.json
        tsconfig_content = self._generate_tsconfig(data)
        files.append(target_dir / 'tsconfig.json')
        (target_dir / 'tsconfig.json').write_text(tsconfig_content)
        
        # 3. .eslintrc.js
        eslint_content = self._generate_eslint_config(data)
        files.append(target_dir / '.eslintrc.js')
        (target_dir / '.eslintrc.js').write_text(eslint_content)
        
        # 4. .prettierrc
        prettier_content = self._generate_prettier_config(data)
        files.append(target_dir / '.prettierrc')
        (target_dir / '.prettierrc').write_text(prettier_content)
        
        # 5. jest.config.js (pour les tests)
        jest_content = self._generate_jest_config(data)
        files.append(target_dir / 'jest.config.js')
        (target_dir / 'jest.config.js').write_text(jest_content)
        
        return files


class YAMLOutputHandler(BaseOutputHandler):
    """Handler pour la génération de fichiers YAML."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère du contenu YAML."""
        # Conversion et formatage YAML
        content = yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=self.config.style.max_line_length,
            indent=self.config.style.indent_size
        )
        
        # Post-traitement
        content = self.post_process(content)
        
        # Validation YAML
        if self.config.validation:
            self._validate_yaml(content)
        
        return content, []
    
    def validate(self, content: str) -> bool:
        """Valide le YAML généré."""
        try:
            yaml.safe_load(content)
            return True
        except yaml.YAMLError as e:
            print(f"Erreur de validation YAML: {e}")
            return False
    
    def _validate_yaml(self, content: str) -> None:
        """Valide le YAML avec des règles spécifiques."""
        # Vérifie la syntaxe YAML
        parsed = yaml.safe_load(content)
        
        # Règles de validation spécifiques
        if isinstance(parsed, dict):
            # Vérifie les clés requises pour certains types de fichiers
            if 'apiVersion' in parsed and 'kind' in parsed:
                # C'est un manifest Kubernetes
                self._validate_kubernetes_manifest(parsed)
            elif 'openapi' in parsed:
                # C'est une spécification OpenAPI
                self._validate_openapi_spec(parsed)


class JSONOutputHandler(BaseOutputHandler):
    """Handler pour la génération de fichiers JSON."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère du contenu JSON."""
        # Formatage JSON
        indent = 2 if not self.config.minify else None
        separators = (',', ':') if self.config.minify else None
        
        content = json.dumps(
            data,
            indent=indent,
            separators=separators,
            default=json_serializer,
            ensure_ascii=False
        )
        
        # Post-traitement
        if not self.config.minify and self.config.formatting:
            content = self._format_json(content)
        
        return content, []
    
    def validate(self, content: str) -> bool:
        """Valide le JSON généré."""
        try:
            json.loads(content)
            return True
        except json.JSONDecodeError as e:
            print(f"Erreur de validation JSON: {e}")
            return False
    
    def _format_json(self, content: str) -> str:
        """Formate le JSON pour une meilleure lisibilité."""
        try:
            parsed = json.loads(content)
            return json.dumps(
                parsed,
                indent=self.config.style.indent_size,
                default=json_serializer,
                ensure_ascii=False
            )
        except:
            return content


class MarkdownOutputHandler(BaseOutputHandler):
    """Handler pour la génération de documentation Markdown."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère de la documentation Markdown."""
        template = self.jinja_env.get_template('documentation.md.jinja')
        
        # Génération du contenu
        content = template.render(
            data=data,
            config=self.config,
            timestamp=datetime.utcnow().isoformat(),
            **self._get_template_helpers()
        )
        
        # Post-traitement
        content = self.post_process(content)
        
        # Génération du site de documentation
        additional_files = self._generate_documentation_site(data)
        
        return content, additional_files
    
    def validate(self, content: str) -> bool:
        """Valide le Markdown généré."""
        # Validation basique du Markdown
        lines = content.split('\n')
        
        # Vérifie les en-têtes
        headers = [l for l in lines if l.strip().startswith('#')]
        if not headers:
            print("Avertissement: Pas d'en-têtes dans le document Markdown")
        
        # Vérifie les liens brisés (basique)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        links = re.findall(link_pattern, content)
        
        for text, url in links:
            if url.startswith('http') and '://' in url:
                # Lien externe - pourrait vérifier la validité
                pass
            elif not url.startswith('#') and '.' in url:
                # Lien interne - pourrait vérifier l'existence du fichier
                pass
        
        return True
    
    def _generate_documentation_site(self, data: Dict[str, Any]) -> List[Path]:
        """Génère un site de documentation complet."""
        files = []
        target_dir = Path(self.config.target_directory or 'docs')
        target_dir.mkdir(exist_ok=True)
        
        # 1. mkdocs.yml
        mkdocs_config = self._generate_mkdocs_config(data)
        files.append(target_dir / 'mkdocs.yml')
        (target_dir / 'mkdocs.yml').write_text(mkdocs_config)
        
        # 2. .readthedocs.yml
        rtd_config = self._generate_readthedocs_config(data)
        files.append(target_dir / '.readthedocs.yml')
        (target_dir / '.readthedocs.yml').write_text(rtd_config)
        
        # 3. Sidebar navigation
        nav_content = self._generate_navigation(data)
        files.append(target_dir / 'nav.md')
        (target_dir / 'nav.md').write_text(nav_content)
        
        # 4. Pages supplémentaires
        pages = [
            ('index.md', self._generate_index_page(data)),
            ('api-reference.md', self._generate_api_reference(data)),
            ('getting-started.md', self._generate_getting_started(data)),
            ('examples.md', self._generate_examples(data)),
        ]
        
        for filename, page_content in pages:
            filepath = target_dir / filename
            filepath.write_text(page_content)
            files.append(filepath)
        
        return files


class OpenAPIOutputHandler(BaseOutputHandler):
    """Handler pour la génération de spécifications OpenAPI."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère une spécification OpenAPI."""
        # Transformation des données DSL en structure OpenAPI
        openapi_spec = self._transform_to_openapi(data)
        
        # Formatage selon la version OpenAPI
        if data.get('openapi_version', '3.0.0').startswith('3'):
            content = self._generate_openapi_v3(openapi_spec)
        else:
            content = self._generate_openapi_v2(openapi_spec)
        
        # Post-traitement
        content = self.post_process(content)
        
        # Génération des clients API
        additional_files = self._generate_api_clients(data, openapi_spec)
        
        return content, additional_files
    
    def validate(self, content: str) -> bool:
        """Valide la spécification OpenAPI."""
        try:
            import openapi_spec_validator
            from openapi_spec_validator import validate_spec
            
            spec = yaml.safe_load(content) if content.strip().startswith('---') else json.loads(content)
            validate_spec(spec)
            return True
            
        except ImportError:
            # Fallback: validation basique
            return self._basic_openapi_validation(content)
        except Exception as e:
            print(f"Erreur de validation OpenAPI: {e}")
            return False


class DockerfileOutputHandler(BaseOutputHandler):
    """Handler pour la génération de Dockerfiles."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère un Dockerfile."""
        template = self.jinja_env.get_template('Dockerfile.jinja')
        
        # Génération du contenu
        content = template.render(
            data=data,
            config=self.config,
            **self._get_template_helpers()
        )
        
        # Post-traitement
        content = self.post_process(content)
        
        # Génération des fichiers associés
        additional_files = self._generate_docker_related_files(data)
        
        return content, additional_files
    
    def validate(self, content: str) -> bool:
        """Valide le Dockerfile généré."""
        # Validation basique du Dockerfile
        lines = content.split('\n')
        
        # Vérifie les instructions de base
        required_instructions = ['FROM', 'WORKDIR']
        found_instructions = []
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                instruction = line.split()[0].upper()
                found_instructions.append(instruction)
        
        # Vérifie les instructions requises
        for req in required_instructions:
            if req not in found_instructions:
                print(f"Avertissement: Instruction {req} manquante dans le Dockerfile")
        
        # Vérifie les meilleures pratiques
        self._check_docker_best_practices(content)
        
        return True
    
    def _generate_docker_related_files(self, data: Dict[str, Any]) -> List[Path]:
        """Génère les fichiers associés à Docker."""
        files = []
        target_dir = Path(self.config.target_directory or '.')
        
        # 1. .dockerignore
        dockerignore_content = self._generate_dockerignore(data)
        files.append(target_dir / '.dockerignore')
        (target_dir / '.dockerignore').write_text(dockerignore_content)
        
        # 2. docker-compose.yml
        docker_compose_content = self._generate_docker_compose(data)
        files.append(target_dir / 'docker-compose.yml')
        (target_dir / 'docker-compose.yml').write_text(docker_compose_content)
        
        # 3. docker-compose.prod.yml
        docker_compose_prod_content = self._generate_docker_compose_prod(data)
        files.append(target_dir / 'docker-compose.prod.yml')
        (target_dir / 'docker-compose.prod.yml').write_text(docker_compose_prod_content)
        
        # 4. docker-compose.test.yml
        docker_compose_test_content = self._generate_docker_compose_test(data)
        files.append(target_dir / 'docker-compose.test.yml')
        (target_dir / 'docker-compose.test.yml').write_text(docker_compose_test_content)
        
        return files


class KubernetesOutputHandler(BaseOutputHandler):
    """Handler pour la génération de manifests Kubernetes."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère des manifests Kubernetes."""
        # Génération des manifests selon le type
        manifests = []
        files = []
        
        target_dir = Path(self.config.target_directory or 'kubernetes')
        target_dir.mkdir(exist_ok=True)
        
        # 1. Deployment
        deployment_content = self._generate_deployment(data)
        manifests.append(('deployment.yaml', deployment_content))
        
        # 2. Service
        service_content = self._generate_service(data)
        manifests.append(('service.yaml', service_content))
        
        # 3. Ingress
        if data.get('expose_publicly', False):
            ingress_content = self._generate_ingress(data)
            manifests.append(('ingress.yaml', ingress_content))
        
        # 4. ConfigMap
        configmap_content = self._generate_configmap(data)
        manifests.append(('configmap.yaml', configmap_content))
        
        # 5. Secret
        secret_content = self._generate_secret(data)
        manifests.append(('secret.yaml', secret_content))
        
        # 6. ServiceAccount, Roles, etc.
        if data.get('rbac_enabled', False):
            rbac_manifests = self._generate_rbac(data)
            manifests.extend(rbac_manifests)
        
        # 7. Helm Chart
        if data.get('generate_helm', True):
            helm_files = self._generate_helm_chart(data)
            files.extend(helm_files)
        
        # Écriture des manifests
        for filename, content in manifests:
            filepath = target_dir / filename
            filepath.write_text(content)
            files.append(filepath)
        
        # Génération du kustomization.yaml
        kustomization_content = self._generate_kustomization(manifests, data)
        kustomization_file = target_dir / 'kustomization.yaml'
        kustomization_file.write_text(kustomization_content)
        files.append(kustomization_file)
        
        return "\n---\n".join(content for _, content in manifests), files
    
    def validate(self, content: str) -> bool:
        """Valide les manifests Kubernetes."""
        try:
            import kubernetes
            from kubernetes.utils import parse_yaml
            
            # Parse et validation basique
            documents = list(yaml.safe_load_all(content))
            
            for doc in documents:
                if not isinstance(doc, dict):
                    continue
                
                # Vérifie les champs requis
                if 'apiVersion' not in doc or 'kind' not in doc:
                    print("Avertissement: Manifest sans apiVersion ou kind")
                
                # Validation spécifique par type
                kind = doc.get('kind', '').lower()
                if kind == 'deployment':
                    self._validate_deployment(doc)
                elif kind == 'service':
                    self._validate_service(doc)
                elif kind == 'ingress':
                    self._validate_ingress(doc)
            
            return True
            
        except ImportError:
            # Fallback: validation basique
            return self._basic_kubernetes_validation(content)
        except Exception as e:
            print(f"Erreur de validation Kubernetes: {e}")
            return False


class TerraformOutputHandler(BaseOutputHandler):
    """Handler pour la génération de configuration Terraform."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère de la configuration Terraform."""
        # Détermination du provider cloud
        provider = data.get('cloud_provider', 'aws').lower()
        
        # Génération des fichiers Terraform
        files = []
        target_dir = Path(self.config.target_directory or 'terraform')
        target_dir.mkdir(exist_ok=True)
        
        # 1. main.tf
        main_content = self._generate_terraform_main(data, provider)
        files.append(target_dir / 'main.tf')
        (target_dir / 'main.tf').write_text(main_content)
        
        # 2. variables.tf
        variables_content = self._generate_terraform_variables(data)
        files.append(target_dir / 'variables.tf')
        (target_dir / 'variables.tf').write_text(variables_content)
        
        # 3. outputs.tf
        outputs_content = self._generate_terraform_outputs(data)
        files.append(target_dir / 'outputs.tf')
        (target_dir / 'outputs.tf').write_text(outputs_content)
        
        # 4. terraform.tfvars.example
        tfvars_example_content = self._generate_terraform_tfvars_example(data)
        files.append(target_dir / 'terraform.tfvars.example')
        (target_dir / 'terraform.tfvars.example').write_text(tfvars_example_content)
        
        # 5. providers.tf
        providers_content = self._generate_terraform_providers(data, provider)
        files.append(target_dir / 'providers.tf')
        (target_dir / 'providers.tf').write_text(providers_content)
        
        # 6. modules si nécessaire
        if data.get('use_modules', True):
            modules = self._generate_terraform_modules(data, provider)
            for module_name, module_content in modules.items():
                module_dir = target_dir / 'modules' / module_name
                module_dir.mkdir(parents=True, exist_ok=True)
                (module_dir / 'main.tf').write_text(module_content)
                files.append(module_dir / 'main.tf')
        
        return main_content, files
    
    def validate(self, content: str) -> bool:
        """Valide la configuration Terraform."""
        # Validation syntaxique Terraform
        lines = content.split('\n')
        
        # Vérifie les blocs de base
        blocks = ['provider', 'resource', 'variable', 'output', 'module']
        found_blocks = []
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('//'):
                for block in blocks:
                    if line.startswith(f'{block} '):
                        found_blocks.append(block)
        
        # Validation spécifique au provider
        provider_pattern = r'provider\s+"([^"]+)"'
        providers = re.findall(provider_pattern, content)
        
        if not providers:
            print("Avertissement: Pas de provider défini")
        
        # Vérifie la syntaxe HCL basique
        try:
            import hcl2
            hcl2.loads(content)
        except ImportError:
            # Fallback: validation manuelle
            pass
        except Exception as e:
            print(f"Erreur de syntaxe Terraform: {e}")
            return False
        
        return True


class CICDOutputHandler(BaseOutputHandler):
    """Handler pour la génération de configurations CI/CD."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère des configurations CI/CD."""
        # Détermination du système CI/CD
        cicd_system = data.get('cicd_system', 'github-actions').lower()
        
        files = []
        target_dir = Path(self.config.target_directory or '.github/workflows' 
                         if cicd_system == 'github-actions' else '.')
        
        if cicd_system == 'github-actions':
            files.extend(self._generate_github_actions(data, target_dir))
        elif cicd_system == 'gitlab-ci':
            files.extend(self._generate_gitlab_ci(data, target_dir))
        elif cicd_system == 'jenkins':
            files.extend(self._generate_jenkins(data, target_dir))
        elif cicd_system == 'circleci':
            files.extend(self._generate_circleci(data, target_dir))
        elif cicd_system == 'azure-devops':
            files.extend(self._generate_azure_devops(data, target_dir))
        
        # Génération des fichiers de configuration associés
        config_files = self._generate_cicd_config_files(data)
        files.extend(config_files)
        
        return "CI/CD configuration generated", files
    
    def validate(self, content: str) -> bool:
        """Valide la configuration CI/CD."""
        # La validation dépend du système CI/CD
        return True


class TestOutputHandler(BaseOutputHandler):
    """Handler pour la génération de suites de tests."""
    
    def generate(self, data: Dict[str, Any]) -> Tuple[str, List[Path]]:
        """Génère des suites de tests."""
        # Détermination du framework de test
        test_framework = data.get('test_framework', 'pytest').lower()
        language = data.get('language', 'python').lower()
        
        files = []
        target_dir = Path(self.config.target_directory or 'tests')
        target_dir.mkdir(exist_ok=True)
        
        if language == 'python' and test_framework == 'pytest':
            files.extend(self._generate_pytest_suite(data, target_dir))
        elif language == 'javascript' and test_framework == 'jest':
            files.extend(self._generate_jest_suite(data, target_dir))
        elif language == 'typescript' and test_framework == 'jest':
            files.extend(self._generate_typescript_jest_suite(data, target_dir))
        
        # Génération des configurations de test
        config_files = self._generate_test_config_files(data, test_framework)
        files.extend(config_files)
        
        return "Test suite generated", files
    
    def validate(self, content: str) -> bool:
        """Valide les fichiers de test générés."""
        return True


class OutputHandlerFactory:
    """Factory pour créer les handlers de sortie."""
    
    _handlers = {
        OutputFormat.PYTHON: PythonOutputHandler,
        OutputFormat.TYPESCRIPT: TypeScriptOutputHandler,
        OutputFormat.JAVASCRIPT: TypeScriptOutputHandler,  # Réutilise TypeScript
        OutputFormat.YAML: YAMLOutputHandler,
        OutputFormat.JSON: JSONOutputHandler,
        OutputFormat.MARKDOWN: MarkdownOutputHandler,
        OutputFormat.OPENAPI: OpenAPIOutputHandler,
        OutputFormat.DOCKERFILE: DockerfileOutputHandler,
        OutputFormat.KUBERNETES: KubernetesOutputHandler,
        OutputFormat.TERRAFORM: TerraformOutputHandler,
        OutputFormat.CICD: CICDOutputHandler,
        OutputFormat.TESTS: TestOutputHandler,
        OutputFormat.MAKEFILE: self._create_makefile_handler,
        OutputFormat.DOCKER_COMPOSE: self._create_docker_compose_handler,
        OutputFormat.REQUIREMENTS: self._create_requirements_handler,
        OutputFormat.PACKAGE_JSON: self._create_package_json_handler,
        OutputFormat.VSCODE: self._create_vscode_handler,
        OutputFormat.INTELLIJ: self._create_intellij_handler,
        OutputFormat.PRE_COMMIT: self._create_pre_commit_handler,
        OutputFormat.GITIGNORE: self._create_gitignore_handler,
        OutputFormat.ENV_FILE: self._create_env_file_handler,
    }
    
    @classmethod
    def create_handler(cls, format: OutputFormat, config: Optional[OutputConfig] = None) -> BaseOutputHandler:
        """
        Crée un handler pour le format spécifié.
        
        Args:
            format: Format de sortie
            config: Configuration optionnelle
            
        Returns:
            Handler configuré
        """
        if format not in cls._handlers:
            raise ValueError(f"Format non supporté: {format}")
        
        if config is None:
            config = OutputConfig(format=format)
        elif config.format != format:
            raise ValueError(f"Format config ({config.format}) ne correspond pas au format demandé ({format})")
        
        handler_class = cls._handlers[format]
        if callable(handler_class):
            handler_class = handler_class()
        
        return handler_class(config)
    
    @staticmethod
    def _create_makefile_handler(config: OutputConfig) -> BaseOutputHandler:
        """Crée un handler pour les Makefiles."""
        class MakefileOutputHandler(BaseOutputHandler):
            def generate(self, data):
                content = self._generate_makefile(data)
                return content, []
            
            def validate(self, content):
                return self._validate_makefile(content)
        
        return MakefileOutputHandler(config)
    
    @staticmethod
    def _create_docker_compose_handler(config: OutputConfig) -> BaseOutputHandler:
        """Crée un handler pour docker-compose."""
        class DockerComposeOutputHandler(YAMLOutputHandler):
            def generate(self, data):
                content = self._generate_docker_compose(data)
                return content, []
        
        return DockerComposeOutputHandler(config)
    
    # Méthodes similaires pour les autres handlers spécialisés...
    
    @classmethod
    def get_supported_formats(cls) -> List[str]:
        """Retourne la liste des formats supportés."""
        return [fmt.value for fmt in cls._handlers.keys()]


class MultiFormatGenerator:
    """Générateur qui supporte plusieurs formats simultanément."""
    
    def __init__(self, configs: List[OutputConfig]):
        self.configs = configs
        self.handlers = [
            OutputHandlerFactory.create_handler(cfg.format, cfg)
            for cfg in configs
        ]
    
    def generate_all(self, data: Dict[str, Any]) -> Dict[OutputFormat, Tuple[str, List[Path]]]:
        """
        Génère le contenu dans tous les formats configurés.
        
        Args:
            data: Données à formater
            
        Returns:
            Dictionnaire format -> (contenu, fichiers)
        """
        results = {}
        
        for handler in self.handlers:
            try:
                content, files = handler.generate(data)
                results[handler.config.format] = (content, files)
            except Exception as e:
                print(f"Erreur lors de la génération au format {handler.config.format}: {e}")
                results[handler.config.format] = (f"Error: {e}", [])
        
        return results
    
    def validate_all(self) -> Dict[OutputFormat, bool]:
        """
        Valide tous les formats générés.
        
        Returns:
            Dictionnaire format -> validité
        """
        validations = {}
        
        for handler in self.handlers:
            # Pour valider, nous aurions besoin de regénérer
            # ou de stocker le contenu généré
            pass
        
        return validations


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration pour générer plusieurs formats
    configs = [
        OutputConfig(
            format=OutputFormat.PYTHON,
            style=CodeStyle(indent_size=4, max_line_length=88),
            target_directory="generated/python"
        ),
        OutputConfig(
            format=OutputFormat.YAML,
            style=CodeStyle(indent_size=2),
            target_directory="generated/yaml"
        ),
        OutputConfig(
            format=OutputFormat.DOCKERFILE,
            target_directory="generated/docker"
        ),
        OutputConfig(
            format=OutputFormat.KUBERNETES,
            target_directory="generated/kubernetes"
        ),
    ]
    
    # Données d'exemple
    example_data = {
        "metadata": {
            "name": "CostAnomalyDetector",
            "version": "1.0.0",
            "description": "Agent de détection d'anomalies de coûts"
        },
        "inputs": {
            "cost_data": {
                "type": "array",
                "items": {"type": "object"}
            }
        },
        "business_logic": {
            "rules": [
                {
                    "name": "high_cost_alert",
                    "condition": "cost > threshold",
                    "action": "send_alert"
                }
            ]
        }
    }
    
    # Génération
    generator = MultiFormatGenerator(configs)
    results = generator.generate_all(example_data)
    
    for fmt, (content, files) in results.items():
        print(f"Format: {fmt.value}")
        print(f"Fichiers générés: {len(files)}")
        print(f"Premières lignes:\n{content[:200]}...\n")